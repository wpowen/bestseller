"""Global outline repetition checks and targeted offline rewrites."""

from copy import deepcopy
import json

from bookengine import q1_plan
from bookengine import outline_diversity as diversity
from bookengine.q1_schema import DATA
from bookengine.storage import save


def outline(n, title, conflict="资源争夺"):
    return dict(
        number=n,
        title=title,
        conflict_type=conflict,
        opening_conflict="想拿回行李",
        scenes=[dict(where="河岸", goal="拿回行李", choice="冒险")],
    )


class Fixture:
    prefix = ""

    def __init__(self, out, replies):
        self.out, self.replies, self.calls, self.cache = out, iter(replies), [], {}

    def stage(self, name, prompt, schema=None, **kw):
        if name not in self.cache:
            self.calls.append((name, prompt))
            self.cache[name] = kw["parser"](json.dumps(next(self.replies), ensure_ascii=False))
        return self.cache[name]


def plan(client, start, end, recent=()):
    return q1_plan.outlines(
        client, {}, {}, DATA["profile"], dict(number=1, end=100), start, end, {}, list(recent), [], []
    )


def test_real_title_pattern_checks_single_nouns_compounds_and_ignores_particles():
    history = [outline(1, "夺闸"), outline(8, "闸前夜"), outline(9, "旧钥匙"), outline(10, "钥匙归谁")]
    batch = [outline(11, "碎闸"), outline(12, "最后的钥匙")]
    issues = diversity.diversity_issues(history, batch)
    assert any(i["kind"] == "title_keyword" and i["keyword"] == "闸" and i["chapters"] == [1, 8, 11] for i in issues)
    assert any(i["kind"] == "title_keyword" and i["keyword"] == "钥匙" for i in issues)
    assert not diversity.diversity_issues(
        [], [outline(n, t, str(n)) for n, t in enumerate(["我的选择", "你的笑容", "他的归途"], 1)]
    )
    assert not diversity.diversity_issues([], [outline(1, "闸闸闸"), outline(2, "闸边")])


def test_conflict_window_spans_batches_and_only_reports_current_batch_targets():
    previous = [outline(n, f"旧章{n}") for n in range(1, 5)]
    current = [outline(5, "夜雨"), outline(6, "星光", "情感抉择")]
    issues = diversity.diversity_issues(previous, current)
    hit = next(i for i in issues if i["kind"] == "conflict_run")
    assert hit["chapters"] == [1, 2, 3, 4, 5]
    assert hit["targets"] == [5]
    assert not diversity.diversity_issues([previous[0], previous[1], previous[3]], current)


def test_global_history_is_loaded_beyond_recent_and_rewrite_touches_only_current_targets(tmp_path):
    save(tmp_path / "chapters/001.json", outline(1, "闸外"))
    save(tmp_path / "chapters/010.json", outline(10, "守闸"))
    recent = [outline(n, f"旧章{n}", str(n)) for n in range(41, 45)]
    batch = [outline(45, "碎闸", "情感抉择"), outline(46, "夜雨", "追逐逃脱")]
    before = (tmp_path / "chapters/001.json").read_bytes()
    replacement = outline(45, "风入高原", "情感抉择")
    client = Fixture(tmp_path, [dict(chapters=batch), dict(chapters=[replacement, outline(46, "不能改")])])
    result = plan(client, 45, 46, recent)
    assert [r["title"] for r in result] == ["风入高原", "夜雨"]
    assert len(client.calls) == 2
    assert "闸" in client.calls[0][1] and "已用核心冲突类型与章名关键词" in client.calls[0][1]
    assert "只重写" in client.calls[1][1] and "45" in client.calls[1][1]
    assert (tmp_path / "chapters/001.json").read_bytes() == before
    report = json.loads(next((tmp_path / "outline-diversity").glob("*.json")).read_text())
    assert report["status"] == "passed" and report["attempts"][0]["targets"] == [45]
    assert "闸" in report["used"]["title_keywords"]


def test_conflict_rewrite_must_change_story_not_only_type_label(tmp_path):
    prior = [outline(n, f"旧章{n}") for n in range(1, 5)]
    for row in prior:
        save(tmp_path / f"writing_outlines/{row['number']:03d}.json", row)
    row = outline(5, "暗潮")
    labels_only = dict(row, conflict_type="情感抉择")
    client = Fixture(tmp_path, [dict(chapters=[row])] + [dict(chapters=[labels_only])] * 3)
    result = plan(client, 5, 5)
    assert result[0]["conflict_type"] == row["conflict_type"]
    assert len(client.calls) == 4
    report_path = next((tmp_path / "outline-diversity").glob("*.json"))
    report = json.loads(report_path.read_text())
    assert report["status"] == "failed_continued" and report["issues"]
    again = plan(client, 5, 5)
    assert again == result and len(client.calls) == 4


def test_repaired_batch_is_history_after_restart_even_without_chapter_record(tmp_path):
    prior = [outline(n, f"旧章{n}", str(n)) for n in (1, 10)]
    for row in prior:
        save(tmp_path / f"chapters/{row['number']:03d}.json", row)
    batch = [outline(11, "归印", "信任抉择")]
    first = Fixture(tmp_path, [dict(chapters=batch)])
    plan(first, 11, 11)
    second = Fixture(tmp_path, [dict(chapters=[outline(21, "山色", "追逐逃脱")])])
    plan(second, 21, 21)
    assert "归印" in second.calls[0][1] or "印" in second.calls[0][1]
    assert "信任抉择" in second.calls[0][1]


def test_legacy_outline_types_survive_and_summary_does_not_mutate_history():
    previous = [dict(number=n, title=f"夜色{n}", structure="公开对质") for n in range(1, 5)]
    before = deepcopy(previous)
    summary = diversity.used_vocabulary(previous)
    assert summary["conflict_types"]
    assert previous == before


def test_long_history_does_not_crowd_out_stage_cast_and_locked_card(tmp_path):
    from test_structure_planning import design, cast

    bible = dict(design(), characters=cast(), selected_card=dict(title="锁定故事", opening="河岸启程"))
    bible["volumes"][4]["location"] = "天穹城"
    row = dict(outline(40, "星月", "信任抉择"), state={"huge": "历史噪声" * 20000}, summary="摘要" * 30000)
    save(tmp_path / "chapters/040.json", row)
    for n in range(1, 40):
        save(tmp_path / f"chapters/{n:03d}.json", outline(n, f"旧章{n}", str(n)))
    client = Fixture(tmp_path, [dict(chapters=[outline(41, "晴空", "追逐逃脱")])])
    q1_plan.outlines(client, bible, {}, DATA["profile"], bible["volumes"][4], 41, 41, {}, [row], [], [])
    prompt = client.calls[0][1]
    assert all(word in prompt for word in ("锁定故事", "天穹城", "对手1", "只能背景", "周远舟", "信任抉择"))
    assert "历史噪声" not in prompt
    assert len(prompt) <= 10000


def test_retired_opponent_is_not_allowed_in_planned_presence_even_after_failed_repair(tmp_path):
    from test_structure_planning import design

    bible = design()
    row = outline(11, "钟声", "信任抉择")
    row["scenes"][0]["characters"] = ["对手1", "同伴"]
    client = Fixture(tmp_path, [dict(chapters=[row])] * 4)
    result = q1_plan.outlines(client, bible, {}, DATA["profile"], bible["volumes"][1], 11, 11, {}, [], [], [])
    assert result[0]["scenes"][0]["characters"] == ["同伴"]
    assert len(client.calls) == 4
    assert "只能背景" in client.calls[0][1]
    report = json.loads(next((tmp_path / "outline-diversity").glob("*.json")).read_text())
    assert report["status"] == "failed_continued"
    assert any(i["kind"] == "opponent_scope" for i in report["issues"])
    assert report["chapters"][0]["background_only"] == ["对手1"]


def test_conflict_rewrite_changes_relevant_scenes_and_keeps_unaffected_chapter(tmp_path):
    for n in range(1, 5):
        save(tmp_path / f"chapters/{n:03d}.json", outline(n, f"旧章{n}"))
    changed = outline(5, "远雷", "信任抉择")
    changed["scenes"][0]["goal"] = "放下行李救同伴"
    sixth = outline(6, "松涛", "追逐逃脱")
    client = Fixture(tmp_path, [dict(chapters=[outline(5, "远雷"), sixth]), dict(chapters=[changed])])
    result = plan(client, 5, 6)
    assert result[0]["conflict_type"] == "信任抉择"
    assert result[0]["scenes"][0]["goal"] == "放下行李救同伴"
    assert result[1]["title"] == sixth["title"]
    assert len(client.calls) == 2


def test_interrupted_repair_resumes_instead_of_accepting_unfinished_report(tmp_path):
    import pytest

    history = [outline(1, "闸口"), outline(2, "守闸")]
    batch = [outline(3, "碎闸")]

    def interrupted(*args):
        raise RuntimeError("simulated process interruption")

    with pytest.raises(RuntimeError):
        diversity.repair_batch(tmp_path, "003-003", history, batch, interrupted)
    report = json.loads((tmp_path / "outline-diversity/003-003.json").read_text())
    assert report["status"] == "repairing"
    attempts = []

    def repaired(current, issues, targets, used, attempt):
        attempts.append(attempt)
        return dict(chapters=[outline(3, "山雨")])

    result = diversity.repair_batch(tmp_path, "003-003", history, batch, repaired)
    assert result[0]["title"] == "山雨" and attempts == [1]
    assert json.loads((tmp_path / "outline-diversity/003-003.json").read_text())["status"] == "passed"


def test_bounded_prompt_keeps_repeated_terms_first_and_persists_complete_vocabulary():
    used = dict(conflict_types={"情感抉择": 4}, title_keywords={f"词{n}": 1 for n in range(1000)})
    used["title_keywords"]["闸"] = 2
    before = deepcopy(used)
    compact = diversity.prompt_vocabulary(used)
    assert compact["title_keywords"]["闸"] == 2
    assert compact["conflict_types"]["情感抉择"] == 4
    assert compact["omitted"]["title_keywords"] > 0
    assert len(json.dumps(compact, ensure_ascii=False)) < 1400
    assert used == before


def test_resume_from_second_chapter_reuses_exhausted_complete_window_without_calls(tmp_path):
    batch = [outline(n, "闸", "资源争夺") for n in range(1, 11)]
    original_client = Fixture(tmp_path, [dict(chapters=batch)] + [{}] * 3)
    original = plan(original_client, 1, 10)
    assert len(original_client.calls) == 4
    save(tmp_path / "chapters/001.json", dict(original[0], fact_source="final_body"))
    path = tmp_path / "outline-diversity/001-010.json"
    assert json.loads(path.read_text())["status"] == "failed_continued"
    before = path.read_bytes(), path.stat().st_mtime_ns
    resumed_client = Fixture(tmp_path, [])
    resumed = plan(resumed_client, 2, 11, [original[0]])
    assert resumed_client.calls == []
    assert resumed == original[1:]
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert not (tmp_path / "outline-diversity/002-011.json").exists()


def test_existing_raw_terminal_window_is_reused_and_next_window_is_generated(tmp_path):
    batch = [outline(n, chr(0x4E30 + n), str(n)) for n in range(1, 11)]
    path = tmp_path / "outline-diversity/001-010.json"
    save(path, dict(status="passed", chapters=batch, attempts=[dict(regeneration=0)], issues=[], used={}))
    before = path.read_bytes()
    resumed_client = Fixture(tmp_path, [])
    remaining = plan(resumed_client, 2, 11)
    assert [r["number"] for r in remaining] == list(range(2, 11))
    assert [r["title"] for r in remaining] == [r["title"] for r in batch[1:]]
    assert resumed_client.calls == [] and path.read_bytes() == before
    next_client = Fixture(tmp_path, [dict(chapters=[outline(11, "晴空", "关系决裂")])])
    next_batch = plan(next_client, 11, 11)
    assert next_batch[0]["number"] == 11 and len(next_client.calls) == 1
    assert path.read_bytes() == before


def test_nonterminal_or_unrelated_windows_are_not_used_for_resume(tmp_path):
    save(tmp_path / "outline-diversity/001-010.json", dict(status="passed", chapters=[outline(1, "旧岸")]))
    save(tmp_path / "outline-diversity/011-020.json", dict(status="repairing", chapters=[outline(11, "未定")]))
    client = Fixture(tmp_path, [dict(chapters=[outline(11, "新路", "关系决裂")])])
    result = plan(client, 11, 11)
    assert result[0]["title"] == "新路" and len(client.calls) == 1
