from copy import deepcopy
from itertools import repeat

from bookengine.rhythm_skeleton import (
    assigned_opening_events,
    bind_opening_events,
    opening_plan_sections,
    stage_content_skeleton,
)
from bookengine.storage import load, save
from test_chapter_rhythm39 import OfflineClient, bible, chapter, plan


PROMISES = [
    "第1章，阿黎拉响雾钟；渡船听见铃声返航。",
    "第2章，船工在雾中打开铜锁。",
    "第2章，船工在雾中打开铜锁。",
    "第3章，阿黎把钥匙交还船主。",
    "读者会看到陌生人渐渐互相信任。",
]


def test_book_scoped_stage_request_keeps_full_due_events_ahead_of_oversized_context(tmp_path):
    class Planner(OfflineClient):
        model, provider = "fixture", "fixture"

    client = Planner(tmp_path, [{"chapters": []}, {"chapters": []}])
    design = dict(bible((2, 4)), opening_promises=PROMISES)
    stage = dict(design["volumes"][0], goal="无关的长背景" * 5000)
    stage_content_skeleton(client, design, stage, [], [{"number": 1}])
    stage_content_skeleton(client, design, design["volumes"][1], [], [{"number": 4}])
    assert len(client.calls) == 2
    first, second = [call[1] for call in client.calls]
    assert first.count("阿黎拉响雾钟") == 1 and "渡船听见铃声返航" in first
    assert first.count("船工在雾中打开铜锁") == 1 and "阿黎把钥匙交还船主" not in first
    assert "陌生人渐渐互相信任" not in first
    assert first.index("船工在雾中打开铜锁") < first.index("无关的长背景")
    assert "阿黎把钥匙交还船主" in second and "拉响雾钟" not in second
    assert opening_plan_sections(PROMISES, 4, 10) == []
    assert assigned_opening_events(PROMISES, 2) == ["第2章，船工在雾中打开铜锁"]
    assert assigned_opening_events(PROMISES, 4) == []


def test_unverified_model_event_never_overrides_or_joins_assigned_event_in_terminal_checkpoint(tmp_path):
    design = dict(bible((3,)), opening_promises=PROMISES)
    authored = "为船主把药送上渡船。"
    source = dict(chapter(2), required_event=authored)
    client = OfflineClient(tmp_path, [{"chapters": [source]}])
    planned = plan(client, design, 2, 2)
    expected = ["第2章，船工在雾中打开铜锁"]
    assert source["required_event"] == authored
    assert planned[0]["required_event"].splitlines() == expected
    assert (
        "结构只组织" in planned[0]["structure_rewrite"] and "优先覆盖冲突旧场景" not in planned[0]["structure_rewrite"]
    )
    assert (
        load(tmp_path / "outline-diversity/002-002.json")["planned_chapters"][0]["required_event"].splitlines()
        == expected
    )
    prompts = [p for stage, p, _ in client.calls if stage.startswith("plan-q1-chapters-")]
    assert prompts and "船工在雾中打开铜锁" in prompts[0] and "阿黎拉响雾钟" not in prompts[0]
    assert "陌生人渐渐互相信任" in prompts[0]
    resumed = OfflineClient(tmp_path)
    assert plan(resumed, design, 2, 2)[0]["required_event"].splitlines() == expected
    assert not resumed.calls


def test_historical_cache_receives_readonly_planning_view_and_undated_chapters_stay_unchanged(tmp_path):
    design = dict(bible((5,)), opening_promises=PROMISES)
    original = [dict(chapter(3), required_event=""), dict(chapter(4, "情感"), required_event="保留第四章自己的锁印")]
    p = tmp_path / "outline-diversity/003-004.json"
    old = dict(
        status="passed", planned_chapters=deepcopy(original), structure_checks_version=100, rhythm_contract_version=100
    )
    save(p, old)
    completed = tmp_path / "chapter-003.body.md"
    save(completed, "已经交付的章节。")
    before = {path: path.read_bytes() for path in (p, completed)}
    client = OfflineClient(tmp_path)
    plan_view = plan(client, design, 3, 4)
    assert plan_view[0]["required_event"] == "第3章，阿黎把钥匙交还船主"
    assert plan_view[1]["required_event"] == original[1]["required_event"]
    assert not client.calls and before == {path: path.read_bytes() for path in before}
    assert bind_opening_events(design, plan_view) == plan_view
    assert "结构只组织" not in plan_view[1].get("structure_rewrite", "")
    assert old["planned_chapters"] == original


def test_later_raw_detail_is_not_promoted_and_previously_adopted_event_survives():
    design = dict(bible((5,)), opening_promises=PROMISES)
    raw = dict(chapter(5), required_event="把本应后续发生的胜负提前")
    result = plan(OfflineClient(None, repeat({"chapters": [raw]})), design, 5, 5)
    assert result[0]["required_event"] == "" and "结构只组织" not in result[0]["structure_rewrite"]
    prior = dict(chapter(2), required_event="已采纳的航路安排")
    recovered = bind_opening_events(design, [prior])[0]
    assert recovered["required_event"].splitlines() == [PROMISES[1].removesuffix("。"), prior["required_event"]]
