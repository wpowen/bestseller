"""Future-only life planning; all client replies are offline and all writes temporary."""

from copy import deepcopy
import json
import io
from pathlib import Path
import shutil
import socket

import pytest

from bookengine.life_planning import apply_life_constraints, confirmed_deaths, validate_life_constraints
from bookengine.life_status import must_survive, writer_survival_instruction
from bookengine.storage import load, save
from tools.runtime_support import ProjectError


BIBLE = {
    "characters": [{"name": "林舟", "role": "protagonist"}, {"name": "沈衡"}, {"name": "陆远"}],
    "antagonists": [{"name": "沈衡", "exit": 10}, {"name": "陆远", "exit": 12}],
}


class OfflineClient:
    prefix = ""

    def __init__(self, out, answers=()):
        self.out, self.answers, self.calls = out, list(answers), []

    def stage(self, name, prompt, schema=None, **kwargs):
        self.calls.append({"stage": name, "prompt": prompt})
        answer = self.answers.pop(0)
        save(self.out / "steps" / (name + ".json"), answer)
        save(self.out / "calls.json", self.calls)
        return answer


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("life-planning70 must be offline")

    monkeypatch.setattr(socket.socket, "connect", fail)


def death(out, number=3, name="沈衡"):
    quote = f"{name}倒地身亡。"
    life = {"value": "dead", "chapter": number, "quote": quote}
    state = {"continuity_ledger": {"characters": {name: {"life_status": life}}}}
    save(out / f"chapter-{number:03d}.body.md", quote)
    save(
        out / f"chapters/{number:03d}.json",
        dict(number=number, status="complete", fact_source="final_body", state=state),
    )
    return state


def plan(number=4, name="沈衡"):
    return {"number": number, "title": "旧阵", "type": "谋划", "scenes": [{"action": f"{name}下令封门。"}]}


def answer(value="生前布下的旧阵开始封门。", path=None):
    return {"changes": [{"path": path or ["scenes", 0, "action"], "value": value}]}


def test_memory_batch_is_revised_without_touching_originals_and_reused(tmp_path):
    state = death(tmp_path)
    original = [plan(), {"number": 5, "title": "歇脚", "scenes": [{"action": "林舟修补药炉。"}]}]
    before = deepcopy(original)
    save(tmp_path / "steps/old.json", original)
    save(tmp_path / "story_bible.yaml", "固定圣经")
    save(tmp_path / "story_design.yaml", "固定设计")
    client = OfflineClient(tmp_path, [answer(path=[0, "scenes", 0, "action"])])
    revised = apply_life_constraints(client, BIBLE, original, state, chapter=4)
    assert revised[0]["scenes"][0]["action"] == "生前布下的旧阵开始封门。"
    assert revised[1] == original[1] and original == before
    assert load(tmp_path / "steps/old.json") == original
    assert (tmp_path / "story_bible.yaml").read_text() == "固定圣经"
    assert (tmp_path / "story_design.yaml").read_text() == "固定设计"
    again = OfflineClient(tmp_path)
    assert apply_life_constraints(again, BIBLE, original, {}, chapter=4) == revised
    assert again.calls == []
    assert list((tmp_path / "life-planning").glob("*.json"))


def test_completed_record_overrides_lagging_run_and_later_missing_snapshot(tmp_path):
    death(tmp_path)
    save(tmp_path / "run.json", {"completed_chapters": 1})
    save(tmp_path / "chapters/004.json", dict(number=4, status="complete", fact_source="final_body", state={}))
    save(tmp_path / "chapter-004.body.md", "林舟离开旧阵。")
    found = confirmed_deaths(tmp_path, {}, chapter=5)
    assert found["沈衡"]["chapter"] == 3
    assert confirmed_deaths(tmp_path, {}, chapter=3) == {}
    original = [plan(4), plan(5)]
    result = apply_life_constraints(
        OfflineClient(tmp_path, [answer(path=[1, "scenes", 0, "action"])]), BIBLE, original, {}, chapter=5
    )
    assert result[0] == original[0]
    assert not validate_life_constraints(result[1], found)


def test_death_quote_must_exist_in_completed_final_body(tmp_path):
    state = death(tmp_path)
    save(tmp_path / "chapter-003.body.md", "沈衡只是负伤。")
    assert confirmed_deaths(tmp_path, state, chapter=4) == {}
    assert apply_life_constraints(OfflineClient(tmp_path), BIBLE, plan(), state, chapter=4) == plan()


def test_volume_view_is_revised_separately(tmp_path):
    death(tmp_path)
    value = {"number": 1, "start": 1, "end": 10, "antagonist": "沈衡", "location": "旧城"}
    client = OfflineClient(tmp_path, [answer("沈衡死后留下的旧阵", ["antagonist"])])
    result = apply_life_constraints(client, BIBLE, value, {}, chapter=4, kind="volume")
    assert result["location"] == value["location"]
    assert result["antagonist"] == "沈衡死后留下的旧阵"
    assert value["antagonist"] == "沈衡"


def test_story_duty_reinjection_is_blocked_at_writer_boundary(tmp_path):
    death(tmp_path)
    value = {"number": 4, "scenes": [], "story_duties": [{"kind": "villain_move", "text": "沈衡主动出手。"}]}
    client = OfflineClient(tmp_path, [answer("沈衡死后旧阵仍在封门。", ["story_duties", 0, "text"])])
    result = apply_life_constraints(client, BIBLE, value, {}, chapter=4)
    assert not validate_life_constraints(result, confirmed_deaths(tmp_path, {}, chapter=4))


def test_dead_participants_are_removed_in_reverse_order_without_role_substitution(tmp_path):
    death(tmp_path)
    death(tmp_path, 2, "陆远")
    original = {"number": 4, "scenes": [{"characters": ["沈衡", "林舟", "陆远"], "action": "林舟面对旧阵。"}]}
    reply = {
        "changes": [
            {"path": ["scenes", 0, "characters", 0], "value": None},
            {"path": ["scenes", 0, "characters", 2], "value": None},
        ]
    }
    result = apply_life_constraints(OfflineClient(tmp_path, [reply]), BIBLE, original, {}, chapter=4)
    assert result["scenes"][0]["characters"] == ["林舟"]
    assert original["scenes"][0]["characters"] == ["沈衡", "林舟", "陆远"]


def test_stable_obligation_ids_and_historical_quotes_are_immutable(tmp_path):
    death(tmp_path)
    value = {
        "number": 4,
        "title": "旧阵",
        "thread_ids": ["沈衡-旧阵"],
        "threads": ["沈衡-旧阵"],
        "thread": "沈衡-旧阵",
        "reader_promise_id": "沈衡-1",
        "story_duties": [
            {
                "id": "rel-沈衡-1",
                "text": "沈衡下令封门。",
                "quote": "沈衡下令封门。",
                "status": "missing",
                "source": "沈衡旧卷纲",
                "thread_id": "沈衡-旧阵",
            }
        ],
    }
    client = OfflineClient(tmp_path, [answer(path=["story_duties", 0, "text"])])
    result = apply_life_constraints(client, BIBLE, value, {}, chapter=4)
    expected = deepcopy(value)
    expected["story_duties"][0]["text"] = "生前布下的旧阵开始封门。"
    assert result == expected
    assert value["story_duties"][0]["text"] == "沈衡下令封门。"
    assert not validate_life_constraints(result, confirmed_deaths(tmp_path, {}, chapter=4))


def test_actual_story_duties_reinjection_preserves_id_and_deletes_dead_names(tmp_path):
    from bookengine.story_duties import with_story_duties

    death(tmp_path)
    bible = dict(
        BIBLE, story_design={"total": 10}, volumes=[{"number": 1, "start": 1, "end": 10, "antagonist": "沈衡"}]
    )
    value = with_story_duties({"number": 4, "type": "谋划", "scenes": []}, bible, tmp_path)
    assert value["story_duties"]
    changes = [
        {"path": hit["path"], "value": None if hit["path"][-2] == "names" else "沈衡死后旧阵仍在封门。"}
        for hit in validate_life_constraints(value, confirmed_deaths(tmp_path, {}, chapter=4))
    ]
    result = apply_life_constraints(OfflineClient(tmp_path, [{"changes": changes}]), bible, value, {}, chapter=4)
    assert [row["id"] for row in result["story_duties"]] == [row["id"] for row in value["story_duties"]]
    assert result["story_duties"][0]["names"] == []
    assert "主动出手" not in result["story_duties"][0]["text"]


@pytest.mark.parametrize("replacement", ["陆远", "沈衡的尸体", "", None])
def test_only_dead_participant_deletion_is_legal(tmp_path, replacement):
    death(tmp_path)
    original = {"number": 4, "scenes": [{"characters": ["沈衡"], "action": "沈衡下令封门。"}]}
    # Null on an action field is not a participant deletion.
    reply = answer(replacement, ["scenes", 0, "action"] if replacement is None else ["scenes", 0, "characters", 0])
    with pytest.raises(ProjectError):
        apply_life_constraints(OfflineClient(tmp_path, [reply, reply]), BIBLE, original, {}, chapter=4)


@pytest.mark.parametrize(
    "reply",
    [
        answer("沈衡假死后下令封门。"),
        answer("沈衡复活，重新下令封门。"),
        answer("沈衡死后仍然下令封门。"),
        answer("回忆沈衡之死，沈衡下令封门。"),
        answer("沈衡的尸体站起，下令封门。"),
        answer("沈衡留下遗物后下令封门。"),
        answer("陆远下令封门。"),
        answer("周冥下令封门。"),
        {"changes": [{"path": ["title"], "value": "另一本故事"}]},
        {"changes": []},
    ],
)
def test_invalid_replans_stop_and_preserve_candidates(tmp_path, reply):
    death(tmp_path)
    original = plan()
    client = OfflineClient(tmp_path, [reply, reply])
    with pytest.raises(ProjectError, match="项目规划无有效结果"):
        apply_life_constraints(client, BIBLE, original, {}, chapter=4)
    assert original == plan()
    assert len(client.calls) == 2
    reports = [load(p) for p in (tmp_path / "life-planning").glob("*.json")]
    assert len(reports) == 2 and all(r["status"] == "rejected" for r in reports)
    assert all(r["candidate"] == reply for r in reports)


def test_multiple_deaths_and_later_new_death_invalidate_prior_revision(tmp_path):
    death(tmp_path)
    original = {"number": 6, "scenes": [{"action": "沈衡下令封门。"}, {"action": "陆远走向药炉。"}]}
    client = OfflineClient(tmp_path, [answer()])
    first = apply_life_constraints(client, BIBLE, original, {}, chapter=6)
    death(tmp_path, 5, "陆远")
    client.answers = [answer("药炉前只剩陆远的遗物。", ["scenes", 1, "action"])]
    revised = apply_life_constraints(client, BIBLE, first, {}, chapter=6)
    assert set(confirmed_deaths(tmp_path, {}, chapter=6)) == {"沈衡", "陆远"}
    assert not validate_life_constraints(revised, confirmed_deaths(tmp_path, {}, chapter=6))


def test_confirmed_dead_are_exempt_from_survival_instructions():
    bible = dict(BIBLE, life_constraints={"沈衡": {"chapter": 3, "quote": "沈衡倒地身亡。"}})
    assert must_survive(bible, 4) == {"陆远": 12}
    assert "沈衡" not in writer_survival_instruction(bible, 4)


def test_recollection_does_not_mask_another_present_action():
    deaths = {"沈衡": {"chapter": 3}}
    assert validate_life_constraints({"action": "沈衡死后仍然下令封门。"}, deaths)
    assert validate_life_constraints({"action": "回忆沈衡之死，沈衡下令封门。"}, deaths)
    assert not validate_life_constraints({"action": "林舟回忆沈衡生前下令封门。"}, deaths)


def test_normalized_outline_cache_is_constrained_before_return(tmp_path, monkeypatch):
    from bookengine import q1_plan

    death(tmp_path)
    original = [dict(plan(), hook="铜铃无风自鸣")]
    cached = {"normalized": True, "end": 4, "chapters": original}
    for name in ("historical_outlines", "cache_replan_issues"):
        monkeypatch.setattr(q1_plan, name, lambda *args: [])
    monkeypatch.setattr(q1_plan, "terminal_window", lambda *args: cached)
    monkeypatch.setattr(q1_plan, "cached_contract_stale", lambda *args: False)
    monkeypatch.setattr(q1_plan, "complete_transitions", lambda chapters, prior: chapters)
    client = OfflineClient(tmp_path, [answer(path=[0, "scenes", 0, "action"])])
    result = q1_plan.outlines(client, BIBLE, {}, {}, {"number": 1, "start": 1, "end": 10}, 4, 4, {}, [], [], [])
    assert not validate_life_constraints(result, confirmed_deaths(tmp_path, {}, chapter=4))
    assert cached["chapters"][0]["scenes"][0]["action"] == "沈衡下令封门。"
    assert len(client.calls) == 1


def test_q1_live_memory_batch_and_volume_are_checked_again_after_death(tmp_path, monkeypatch):
    from test_run_to_finish import q1_fixture, good
    from unittest.mock import patch

    q1, client, config, _, _ = q1_fixture(tmp_path, monkeypatch)
    bible, threads, profile = q1.prepare()
    bible["characters"].extend(BIBLE["characters"][1:])
    bible["antagonists"] = BIBLE["antagonists"]
    original = [plan(1), plan(2)]
    monkeypatch.setattr(q1, "outlines", lambda *args, **kwargs: original)
    monkeypatch.setattr(q1, "volume", lambda *args, **kwargs: dict(number=1, start=1, end=2, antagonist="沈衡"))
    received = []

    def writer(client, book, bible, threads, profile, vol, outline, state, history, rows, out, style, data):
        number = outline["number"]
        received.append(deepcopy(outline))
        current = deepcopy(state)
        if number == 1:
            current.update(death(out, 1))
        else:
            assert "沈衡" not in must_survive(bible, number)
            assert not validate_life_constraints(outline, confirmed_deaths(out, {}, chapter=number))
            assert not validate_life_constraints(vol, confirmed_deaths(out, {}, chapter=number))
            save(out / f"chapter-{number:03d}.body.md", "阿宁面对旧阵。")
        row = dict(
            number=number,
            title="旧阵",
            status="complete",
            chars=200,
            within_band=True,
            state=current,
            issues=[],
            summary="阿宁面对旧阵。",
            seconds=0,
            style={"dialogue_ratio": 0.3},
            fact_source="final_body",
        )
        save(out / f"chapters/{number:03d}.json", row)
        return row

    monkeypatch.setattr(q1, "chapter", writer)
    replies = [
        good(json.dumps(answer("沈衡死后留下的旧阵", ["antagonist"]), ensure_ascii=False)),
        good(json.dumps(answer(path=[1, "scenes", 0, "action"]), ensure_ascii=False)),
    ]
    with patch("urllib.request.urlopen", side_effect=replies):
        result = q1.run_q1(config, tmp_path, tmp_path / "unused.env", tmp_path)
    assert result["status"] == "complete" and result["completed_chapters"] == 2
    assert len(client.calls) == 2 and len(received) == 2
    assert original[1]["scenes"][0]["action"] == "沈衡下令封门。"
    saved = {p: p.read_bytes() for p in [*tmp_path.glob("chapter-*.body.md"), *tmp_path.glob("chapters/*.json")]}
    meta = load(tmp_path / "run.json")
    save(tmp_path / "run.json", dict(meta, status="interrupted", completed_chapters=0))
    from bookengine.q1_chapter import chapter as actual_chapter

    monkeypatch.setattr(q1, "chapter", actual_chapter)
    with patch("urllib.request.urlopen", side_effect=AssertionError("completed records must not call model")):
        resumed = q1.run_q1(config, tmp_path, tmp_path / "unused.env", tmp_path)
    assert resumed["completed_chapters"] == 2
    assert all(path.read_bytes() == body for path, body in saved.items())


def test_real_eval65_saved_death_temporary_copy(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2].parent / "bestseller-tmp/web65n-ch50-copy"
    if not (source / "chapters/031.json").is_file():
        pytest.skip("saved eval65 source unavailable")
    paths = [source / "chapters/031.json", source / "chapter-031.body.md"]
    before = {p: p.read_bytes() for p in paths}
    for path in paths:
        target = tmp_path / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    state = load(tmp_path / "chapters/031.json")["state"]
    found = confirmed_deaths(tmp_path, {}, chapter=33)
    assert found["顾夏珩"]["chapter"] == 31
    assert found["顾夏珩"]["quote"] in (tmp_path / "chapter-031.body.md").read_text()
    value = {"number": 33, "scenes": [{"action": "外廊尽头传来顾夏珩的笑声。"}]}
    from bookengine import client as engine_client

    monkeypatch.setattr(engine_client, "credentials", lambda *args: ("https://offline.invalid/v1", "offline-test"))
    reply = {
        "choices": [
            {
                "message": {"content": json.dumps(answer("外廊只剩顾夏珩生前留下的封火阵。"), ensure_ascii=False)},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }
    monkeypatch.setattr(
        engine_client.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(json.dumps(reply).encode())
    )
    client = engine_client.Client(tmp_path, tmp_path / "unused.env", "")
    result = apply_life_constraints(client, {"characters": [{"name": "顾夏珩"}]}, value, state, chapter=33)
    assert not validate_life_constraints(result, found)
    assert load(tmp_path / "calls.json")[0]["status"] == "ok"
    assert load(tmp_path / "responses/001.json")["usage"]["total_tokens"] == 20
    assert all(p.read_bytes() == body for p, body in before.items())
