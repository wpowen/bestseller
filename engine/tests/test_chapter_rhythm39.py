"""Planning-only rhythm contracts and recovery; no network or real library writes."""

from copy import deepcopy
import json

import pytest

from bookengine import q1_plan
from bookengine import outline_diversity as diversity
from bookengine.q1_schema import DATA
from bookengine.storage import save


def bible(lengths=(10, 10)):
    start, stages = 1, []
    for i, length in enumerate(lengths, 1):
        stages.append(dict(number=i, start=start, end=start + length - 1, location=f"地点{i}"))
        start += length
    return dict(volumes=stages)


def chapter(n, kind="对抗"):
    intentions = {
        "情感": ("修复同伴间的信任", "向同伴坦白隐瞒并重新约定分工"),
        "关系": ("修复同伴间的信任", "向同伴坦白隐瞒并重新约定分工"),
        "收获": ("领取行动赢得的回报", "试用奖励后决定与同伴分配"),
        "揭秘": ("核对线索里的矛盾", "用证据推翻原判断并改变去向"),
        "谋划": ("比较两种方案的风险", "放弃高风险方案并选定分工"),
        "日常": ("在驿站休整疗伤", "吃饭后整理行装再赶路"),
    }
    goal, choice = intentions.get(kind, ("击败守卫", "拔剑击退守卫"))
    return dict(
        number=n,
        title=chr(0x4E00 + n),
        type=kind,
        conflict_type=str(n),
        opening_conflict="守卫阻拦主角取回行李",
        scenes=[dict(where="河岸", characters=["主角", "同伴"], goal=goal, choice=choice)],
    )


class OfflineClient:
    prefix = ""

    def __init__(self, out, replies=()):
        self.out, self.replies, self.calls = out, iter(replies), []

    def stage(self, name, prompt, schema=None, **kw):
        self.calls.append((name, prompt, schema))
        return kw["parser"](json.dumps(next(self.replies), ensure_ascii=False))


def plan(client, design, start, end):
    stage = next(s for s in design["volumes"] if s["start"] <= start <= s["end"])
    return q1_plan.outlines(client, design, {}, DATA["profile"], stage, start, end, {}, [], [], [])


def rhythm():
    from bookengine import chapter_rhythm

    return chapter_rhythm


@pytest.mark.parametrize("lengths", [(10,) * 5, (1,), (2,), (3,), (11, 1), (25, 25)])
def test_shared_slots_validate_across_batches_and_short_stage_tails(lengths):
    module, design, history = rhythm(), bible(lengths), []
    for stage in design["volumes"]:
        for start in range(stage["start"], stage["end"] + 1, 3):
            end = min(start + 2, stage["end"])
            slots = module.rhythm_slots(design, start, end, history)
            batch, _ = module.repair_rhythm(design, history, [chapter(s["number"], s["rhythm_type"]) for s in slots])
            assert not module.rhythm_issues(design, history, batch)
            history += batch
    for stage in design["volumes"]:
        assert any(r["type"] == "情感" for r in history if stage["start"] <= r["number"] <= stage["end"])
    for i in range(len(history) - 3):
        assert [r["type"] for r in history[i : i + 4]] != ["对抗"] * 4


def test_cross_stage_history_requires_allowed_followup_not_daily_or_relabelled_combat():
    module = rhythm()
    history = [chapter(n) for n in (8, 9, 10)]
    for kind in ("对抗", "日常", "收获", "关系", "揭秘", "谋划"):
        relabelled = chapter(11)
        relabelled["type"] = kind
        issues = module.rhythm_issues(bible(), history, [relabelled])
        assert issues, kind  # Noncombat labels still contain the original battle scenes.
        assert all(i["targets"] == [11] for i in issues)
    slots = module.rhythm_slots(bible(), 11, 11, history)
    assert slots[0]["rhythm_type"] in ("收获", "情感", "揭秘", "谋划")
    assert module.rhythm_slots(bible(), 11, 11, [chapter(8), chapter(10)])[0]["rhythm_type"] == "对抗"


def test_existing_emotional_or_relationship_main_scenes_satisfy_stage_contract():
    module = rhythm()
    for kind in ("情感", "关系"):
        history = [chapter(1, kind)]
        history[0]["scenes"] = [dict(goal="修复同伴间的信任", choice="向同伴坦白隐瞒并重新约定分工")]
        current = [chapter(10)]
        before = deepcopy((history, current))
        assert not module.rhythm_issues(bible(), history, current)
        assert (history, current) == before


def test_exhausted_model_repair_keeps_original_after_two_attempts_and_records(tmp_path):
    module, design = rhythm(), bible((4,))
    batch = [chapter(n) for n in range(1, 5)]
    batch[-1].update(transition="沿河岸走到旧栈桥", core_required=True, core_application="借影复制开锁动作使铁门打开")
    before, calls = deepcopy(batch), []

    def relabel(current, issues, targets, used, attempt):
        calls.append(attempt)
        return dict(chapters=[dict(r, type="情感") for r in current if r["number"] in targets])

    result = diversity.repair_batch(tmp_path, "001-004", [], batch, relabel, bible=design)
    assert calls == [1, 2]
    assert result == before
    report = json.loads((tmp_path / "outline-diversity/001-004.json").read_text())
    assert report["rhythm_contract_version"] == module.VERSION
    assert report["status"] == "failed_continued"
    assert report["rhythm_replans"][0]["action"] == "kept_original"
    assert any(i["kind"].startswith("rhythm_") for i in report["issues"])
    assert batch == before


@pytest.mark.parametrize("normalized", [False, True])
def test_resume_rechecks_old_or_current_cache_only_rewrites_unwritten_suffix(tmp_path, normalized):
    module, design = rhythm(), bible((5,))
    batch = [chapter(n) for n in range(1, 6)]
    path = tmp_path / "outline-diversity/001-005.json"
    report = dict(status="failed_continued", chapters=batch, attempts=[], structure_checks_version=999)
    if normalized:
        report["planned_chapters"] = batch
        report["rhythm_contract_version"] = module.VERSION
    save(path, report)
    for row in batch[:3]:
        save(tmp_path / f"chapters/{row['number']:03d}.json", dict(row, fact_source="final_body"))
        save(tmp_path / f"chapter-{row['number']:03d}.body.md", "已写正文不可改")
    frozen = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    client = OfflineClient(tmp_path, [dict(chapters=[chapter(4, "情感")])])
    result = plan(client, design, 4, 5)
    assert [r["number"] for r in result] == [4, 5]
    assert not module.rhythm_issues(design, batch[:3], result)
    assert result[0]["type"] == "情感" and result[0]["scenes"] != batch[3]["scenes"]
    assert len(client.calls) == 1  # Migrate saved plans; no new full-window generation.
    assert all(p.read_bytes() == data for p, data in frozen.items())
    client2 = OfflineClient(tmp_path)
    assert plan(client2, design, 4, 5) == result
    assert client2.calls == []


def test_new_generation_gets_same_required_types_focus_and_schema_as_validator(tmp_path):
    module, design = rhythm(), bible((4,))
    client = OfflineClient(
        tmp_path, [dict(chapters=[chapter(n) for n in range(1, 5)]), dict(chapters=[chapter(4, "情感")])]
    )
    result = plan(client, design, 1, 4)
    assert not module.rhythm_issues(design, [], result)
    prompt, schema = client.calls[0][1:]
    for field in ("rhythm_type", "rhythm_focus", "rhythm_contract_version"):
        assert field in prompt and field in schema
        assert field in result[3]
    assert result[3]["type"] == result[3]["rhythm_type"] == "情感"
    assert "人物关系" in result[3]["rhythm_focus"]
    assert "击败守卫" not in json.dumps(result[3]["scenes"], ensure_ascii=False)


def test_plan_with_no_stage_still_enforces_cross_batch_combat_run(tmp_path):
    module = rhythm()
    for n in (1, 2, 3):
        save(tmp_path / f"chapters/{n:03d}.json", chapter(n))
    client = OfflineClient(tmp_path, [dict(chapters=[chapter(4)]), dict(chapters=[chapter(4, "情感")])])
    result = q1_plan.outlines(client, {}, {}, DATA["profile"], dict(number=1, end=25), 4, 4, {}, [], [], [])
    assert not module.rhythm_issues({}, [chapter(n) for n in (1, 2, 3)], result)
    assert result[0]["type"] in ("收获", "情感", "揭秘", "谋划")


def test_normalization_filled_types_are_checked_and_restart_reuses_result_without_calls(tmp_path):
    module = rhythm()
    scheduled = q1_plan.schedule(1, 10, DATA["profile"])
    slots = module.rhythm_slots({}, 1, 10, [], scheduled)
    batch = [dict(chapter(slot["number"], slot["rhythm_type"]), type="") for slot in slots]
    client = OfflineClient(tmp_path, [dict(chapters=batch)])
    args = ({}, {}, DATA["profile"], dict(number=1, end=25))
    result = q1_plan.outlines(client, *args, 1, 10, {}, [], [], [])
    assert not module.rhythm_issues({}, [], result)
    report = json.loads((tmp_path / "outline-diversity/001-010.json").read_text())
    assert not report["rhythm_repairs"]
    assert not report["rhythm_issues_after"]
    resumed = OfflineClient(tmp_path)
    assert q1_plan.outlines(resumed, *args, 2, 10, {}, [result[0]], [], []) == result[1:]
    assert not resumed.calls


def test_relationship_alias_survives_normalization_and_retains_real_scene_intent(tmp_path):
    design, row = bible((1,)), chapter(1, "关系")
    row["scenes"] = [dict(goal="向同伴坦白隐瞒并恢复信任", choice="同伴拒绝原来的分工，双方重新约定边界")]
    client = OfflineClient(tmp_path, [dict(chapters=[row])])
    result = plan(client, design, 1, 1)
    assert result[0]["type"] == "情感"
    assert result[0]["scenes"][0]["choice"] == row["scenes"][0]["choice"]
    assert len(client.calls) == 1


def test_completed_chapter_matching_saved_plan_does_not_retry_exhausted_future_issues(tmp_path):
    module, design = rhythm(), bible((5,))
    batch = [chapter(n) for n in range(1, 6)]
    issues = module.rhythm_issues(design, [], batch)
    assert issues
    save(
        tmp_path / "outline-diversity/001-005.json",
        dict(
            status="failed_continued",
            chapters=batch,
            planned_chapters=batch,
            structure_checks_version=999,
            rhythm_contract_version=module.VERSION,
            rhythm_issues_after=issues,
            plot_issues_after=[],
        ),
    )
    save(tmp_path / "writing_outlines/001.json", batch[0])
    save(tmp_path / "chapters/001.json", dict(batch[0], fact_source="final_body"))
    client = OfflineClient(tmp_path, [dict(chapters=[])] * 4)

    result = plan(client, design, 2, 5)

    assert [(row["number"], row["title"], row["type"]) for row in result] == [
        (row["number"], row["title"], row["type"]) for row in batch[1:]
    ]
    assert client.calls == []


def test_completed_chapter_type_drift_replans_the_affected_future_suffix(tmp_path):
    module, design = rhythm(), bible((5,))
    batch = [chapter(1), chapter(2), chapter(3, "谋划"), chapter(4, "情感"), chapter(5)]
    save(
        tmp_path / "outline-diversity/001-005.json",
        dict(
            status="passed",
            chapters=batch,
            planned_chapters=batch,
            structure_checks_version=999,
            rhythm_contract_version=module.VERSION,
            rhythm_issues_after=[],
            plot_issues_after=[],
        ),
    )
    save(tmp_path / "writing_outlines/001.json", batch[0])
    save(tmp_path / "chapters/001.json", dict(batch[0], type="谋划", fact_source="final_body"))
    replacement = chapter(2, "谋划")
    client = OfflineClient(tmp_path, [dict(chapters=[replacement])])

    result = plan(client, design, 2, 5)

    assert client.calls
    assert result[0]["number"] == 2 and result[0]["type"] == "谋划"
    assert [(row["number"], row["title"], row["type"]) for row in result[1:]] == [
        (row["number"], row["title"], row["type"]) for row in batch[2:]
    ]


def test_observed_drift_issue_keeps_semantic_observed_scalar_for_diversity_fallback(tmp_path):
    from bookengine.planning_drift import cache_replan_issues

    batch = [chapter(1), chapter(2, "谋划")]
    report_path = tmp_path / "outline-diversity/001-002.json"
    save(report_path, dict(chapters=batch, rhythm_issues_after=[], plot_issues_after=[], plot_history=[]))
    observed = dict(
        batch[0],
        fact_source="final_body",
        plan_alignment=dict(
            status="mismatch",
            mismatches=[
                dict(field="type", expected="对抗", observed="谋划"),
                dict(field="hook_type", expected="threat", observed="关系裂痕"),
            ],
        ),
    )
    cached = dict(path=str(report_path), chapters=[batch[1]])

    issue = next(
        row for row in cache_replan_issues({}, [observed], cached) if row["kind"] == "rhythm_observed_plan_drift"
    )

    assert issue["observed"] == "谋划"
    assert issue["observed_mismatches"] == observed["plan_alignment"]["mismatches"]
    assert hash(issue["observed"])


def test_plan_alignment_uses_explicit_observations_and_keeps_missing_events_unknown():
    from bookengine.planning_drift import observe_plan_alignment

    outline = dict(type="情感", hook_type="关系破裂", structure="情感摊牌", required_event="同伴公开决裂")
    matched = observe_plan_alignment(
        outline,
        dict(type="情感", hook_type="关系破裂", structure="情感摊牌", required_event_fulfilled=True),
    )
    drifted = observe_plan_alignment(outline, dict(type="对抗", hook_type="关系破裂", structure="情感摊牌"))
    unknown = observe_plan_alignment(outline, dict(type="情感", hook_type="关系破裂"))

    assert matched["status"] == "matched"
    assert drifted["status"] == "mismatch" and drifted["mismatches"][0]["field"] == "type"
    assert unknown["status"] == "unknown" and set(unknown["unknown"]) == {"structure", "required_event"}
