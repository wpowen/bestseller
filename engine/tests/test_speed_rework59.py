"""Regression contracts for story-layer 59 speed rework."""

from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from bookengine.chapter_rhythm import normalize_chapter_type
from bookengine.client import Client, ProjectError
from bookengine.outline_diversity import repair_batch
from bookengine.planning_policy import hard_replan_issues, planning_window_end
from bookengine.rhythm_replanning import model_review_issues
from bookengine.rhythm_writer import writer_main_scene_contract, writer_rhythm_instruction


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("打", "对抗"),
        ("救", "对抗"),
        ("爽点", "对抗"),
        ("能力", "对抗"),
        ("动作", "对抗"),
        ("收束", "对抗"),
        ("能力兑现", "对抗"),
        ("对抗兑现", "对抗"),
        ("阶段兑现", "对抗"),
        ("关系推进", "情感"),
        ("关系", "情感"),
        ("战斗", "对抗"),
        ("打斗", "对抗"),
        ("解谜", "揭秘"),
        ("推理", "揭秘"),
        ("谋略", "谋划"),
        ("关系章", "情感"),
        ("身份揭露", "揭秘"),
        ("潜入揭密", "揭秘"),
        ("潜入营救", "对抗"),
        ("救援推进", "对抗"),
        ("连续闯关", "对抗"),
        ("潜入试探", "对抗"),
    ],
)
def test_chapter_type_aliases_are_normalized_before_comparison(value, expected):
    assert normalize_chapter_type(value) == expected
    assert not model_review_issues(
        [{"number": 1, "type": expected}],
        [{"number": 1, "content_type": value, "status": "mismatch", "reason": "旧词表"}],
    )


def test_unknown_chapter_type_never_enters_comparisons_as_a_private_vocabulary():
    assert normalize_chapter_type("自创章型") == ""


def test_extraction_planning_and_checkup_share_the_same_canonical_types():
    from bookengine.plot_diagnostics import plot_findings
    from bookengine.q1_extract import normalize_extract
    from bookengine.q1_plan import schedule

    assert normalize_extract({"type": "动作", "state": {}})["type"] == "对抗"
    assert (
        schedule(3, 3, {"chapter_type_mix": {"对抗": 30, "情感": 15}}, [{"type": "打"}, {"type": "动作"}])[0]["type"]
        != "对抗"
    )
    findings = plot_findings(
        {"volumes": [{"number": 1, "start": 1, "end": 1}]},
        [{"number": 1, "meta": {"type": "关系推进", "solutions": []}, "text": "两人当面坦白。"}],
    )
    assert findings["chapter_rhythm"]["distribution"] == {"关系": 1}
    assert findings["chapter_rhythm"]["stages"][0]["status"] == "met"


def test_only_stage_hard_constraints_are_replan_eligible():
    issues = [
        {"kind": "title_keyword", "targets": [1]},
        {"kind": "rhythm_content_mismatch", "targets": [2]},
        {"kind": "rhythm_model_mismatch", "targets": [3]},
        {"kind": "rhythm_stage_relation", "targets": [4]},
        {"kind": "rhythm_combat_run", "targets": [5]},
        {"kind": "reader_promise_payoff", "targets": [6]},
        {"kind": "opponent_resolution", "targets": [7]},
    ]

    assert hard_replan_issues(issues) == issues[3:]


def test_planning_windows_never_exceed_five_chapters():
    assert planning_window_end(1, 20, 10) == 5
    assert planning_window_end(6, 20, 10) == 10
    assert planning_window_end(19, 20, 20) == 20


def _combat(number, title):
    return {
        "number": number,
        "title": title,
        "type": "对抗",
        "opening_conflict": "追兵封路",
        "scenes": [{"goal": "击退追兵", "action": "拔刀突围", "result": "暂时脱身"}],
    }


def test_title_repetition_is_kept_without_calling_outline_rewrite(tmp_path):
    batch = [_combat(1, "雪兽来袭"), _combat(2, "雪兽伏击"), _combat(3, "雪兽围堵")]

    def forbidden(*_):
        raise AssertionError("title_keyword must not trigger a model rewrite")

    result = repair_batch(tmp_path, "001-003", [], batch, forbidden, bible={})

    assert result[:2] == batch[:2]
    assert result == batch
    report = json.loads((tmp_path / "outline-diversity/001-003.json").read_text())
    assert report["title_renames"][0]["number"] == 3


def test_replan_targets_at_most_two_chapters_and_retries_once(tmp_path):
    original = [_combat(number, chr(0x4E00 + number)) for number in range(1, 6)]
    calls = []

    def unchanged(current, issues, targets, used, attempt):
        calls.append((targets, attempt))
        return {"chapters": [deepcopy(row) for row in current if row["number"] in targets]}

    repair_batch(
        tmp_path,
        "001-005",
        [],
        original,
        unchanged,
        bible={"volumes": [{"number": 1, "start": 1, "end": 5}]},
        initial_issues=[{"kind": "rhythm_stage_relation", "targets": [3, 4, 5]}],
    )

    assert len(calls) == 1 and calls[0][1] == 1
    assert 1 <= len(calls[0][0]) <= 2
    report = json.loads((tmp_path / "outline-diversity/001-005.json").read_text())
    assert report["replan_decisions"]
    assert all(row["action"] == "kept_original" and row["attempts"] == 1 for row in report["replan_decisions"])


class Reply:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def read(self):
        return json.dumps(
            {"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}], "usage": {}}
        ).encode()


class OkReply(Reply):
    def read(self):
        return json.dumps({"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}], "usage": {}}).encode()


def test_truncated_response_is_recorded_separately_and_retried_with_larger_cap(tmp_path):
    with patch("bookengine.client.credentials", return_value=("https://fake.invalid/v1", "fake-key")):
        client = Client(tmp_path, tmp_path / "unused.env", "")
    caps = []
    original = client.request

    def spy(name, full, cap, *rest):
        caps.append(cap)
        return original(name, full, cap, *rest)

    client.request = spy
    with patch("urllib.request.urlopen", side_effect=[Reply(), OkReply()]) as http:
        assert client.stage("r2-ch6-cross-chapter-opening", "prompt", "{}", cap=1400) == {}

    assert http.call_count == 2
    assert [c["status"] for c in client.calls] == ["truncated", "ok"]
    assert caps[0] == 1400 and caps[1] >= 16000


def test_repeated_truncation_still_fails_the_stage_not_silently(tmp_path):
    with patch("bookengine.client.credentials", return_value=("https://fake.invalid/v1", "fake-key")):
        client = Client(tmp_path, tmp_path / "unused.env", "")
    with patch("urllib.request.urlopen", side_effect=lambda *a, **k: Reply()):
        with pytest.raises(ProjectError):
            client.stage("plan-q1-chapters-1-5", "prompt", "{}")
    assert all(c["status"] == "truncated" for c in client.calls)


def test_writer_contract_leads_with_exact_type_hook_and_scene_actions():
    outline = {
        "type": "情感",
        "hook_type": "choice",
        "hook": "霜砚秋把钥匙推回顾川序面前，等他决定是否坦白。",
        "rhythm_focus": "两人因隐瞒伤势重新划定信任边界",
        "scenes": [
            {"goal": "问清伤势", "action": "顾川序承认隐瞒", "choice": "交出路线决定权", "result": "暂时恢复合作"}
        ],
    }

    contract = writer_main_scene_contract(outline)
    instruction = writer_rhythm_instruction(outline)

    assert contract["chapter_type"] == "情感"
    assert contract["hook"] == outline["hook"]
    assert contract["scenes"][0]["action"] == "顾川序承认隐瞒"
    assert instruction.startswith("本章主场景硬合同")
    assert all(value in instruction for value in (outline["hook"], "顾川序承认隐瞒", "交出路线决定权"))
