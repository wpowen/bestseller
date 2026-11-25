"""Exercise the actual writer, transport ledger and extraction without outside calls."""

import json
import re
from pathlib import Path

import pytest

from bookengine.q1_extract import pack
from bookengine.storage import load, save
from bookengine.text import number_paragraphs
from tools.fiction_trial import VOLUME, run_trial, digest, json_hash
from tools.runtime_support import ProjectError


BOOK = Path(__file__).resolve().parents[2] / "books/webui/24ddbcc8-b588-4a84-885c-dab09edc95a2"


def fixture_source(path):
    card = dict(
        title="断墨", premise="顾寒守着库房", opening="今晨门上多了一道锁", golden_finger_rule="旧印可以留下影子"
    )
    person = dict(name="顾寒", role="protagonist", identity="库房学徒", gender="男")
    canon = dict(genre_kind="爽文", payoff_menu=["收获"], hook_types=["clue"], ladder={"strength": ["学徒", "墨匠"]})
    bible = dict(
        title=card["title"],
        selected_card=card,
        characters=[person],
        canon=canon,
        power=dict(name="旧印", rule=card["golden_finger_rule"]),
        volumes=[dict(number=1, start=1, end=10)],
        initial_dashboard=dict(strength="学徒"),
        opening_promises=["第2章，学徒把押印的收条交给掌柜。"],
    )
    save(
        path / "book.yaml",
        dict(prompt_pack="xianxia-upgrade-core", chapters=50, words_per_chapter=2200, user_input="墨"),
    )
    save(path / "story_bible.yaml", bible)
    save(path / "threads.yaml", dict(threads=[]))
    save(path / "story_design.yaml", dict(total=50))
    save(path / VOLUME, dict(number=1, start=1, end=10))
    for n in range(1, 5):
        scene = dict(
            characters=["顾寒"],
            goal="找出锁孔",
            obstacle="门缝太窄",
            action="顾寒把印面按在门缝上",
            choice="留下印子",
            result="门锁的纹路现了形",
        )
        save(
            path / f"writing_outlines/{n:03d}.json",
            dict(
                number=n,
                title="铜锁",
                type="对抗",
                opening_conflict="晨光已进库房",
                required_event="第2章，学徒把押印的收条交给掌柜。\n旧卷误写学徒已经夺回印簿。" if n in (2, 4) else "",
                scenes=[scene],
                threads=[],
                hook="铜锁响了",
            ),
        )
    return path


@pytest.fixture(params=["portable", "historical"])
def flight(tmp_path, monkeypatch, request):
    if request.param == "historical" and not (BOOK / "book.yaml").exists():
        pytest.skip("historical production source unavailable")
    source = BOOK if request.param == "historical" else fixture_source(tmp_path / "source")
    from bookengine import client

    out = tmp_path / "sample"
    monkeypatch.setattr(client, "credentials", lambda *a: ("https://test.invalid", "mock"))
    stages, reject = [], set()

    class OfflineResponse:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def reply(request, **kwargs):
        stage = load(out / "calls.json")[-1]["stage"]
        stages.append(stage)
        n = int(re.search(r"ch(\d+)-", stage)[1])
        title = f"顾寒在门前刻下第{n}个字。"
        if stage.endswith("-draft"):
            content = f"{title}\n\n他合上门。"
            if n == 3 and source != BOOK:
                content += "\n\n" + "墨" * 8500 + "，他才把手收回来。"
        elif "-trial-state-" in stage:
            payload = json.loads(request.data)
            assert (
                number_paragraphs((out / f"chapter-{n:03d}.body.md").read_text(encoding="utf-8"))
                in payload["messages"][1]["content"]
            )
            content = pack(
                dict(
                    type="对抗",
                    hook_type="clue",
                    state={},
                    summary=title,
                    payoff=dict(type="无兑现", fulfilled=False),
                    continuity_delta=dict(characters=[dict(name="顾寒", field="location", value="门前", quote=title)]),
                )
            )
            if n in reject:
                content = "{}"
        else:
            pytest.fail(f"unexpected model call {stage}")
        return OfflineResponse(
            dict(
                model="MiniMax-M3.1-Flash",
                choices=[dict(message=dict(content=content), finish_reason="stop")],
                usage=dict(prompt_tokens=11, completion_tokens=6),
            )
        )

    monkeypatch.setattr(client.urllib.request, "urlopen", reply)
    monkeypatch.setattr(client, "read_with_deadline", lambda response, *a: json.dumps(response.value).encode())
    return source, out, stages, reject


@pytest.mark.parametrize("arm", ["A", "B"])
def test_sample_resumes_from_extraction_and_preserves_history(flight, arm):
    source, out, stages, reject = flight
    reject.add(2)
    with pytest.raises(ProjectError, match="fact extraction failed"):
        run_trial(source, out, None, arm, through=2)
    assert load(out / "run.json")["completed_chapters"] == 1
    assert stages.count("r2-ch2-draft") == 1
    reject.clear()
    result = run_trial(source, out, None, arm)
    assert result["status"] == "sample_complete"
    assert stages.count("r2-ch2-draft") == 1
    for n in range(1, 5):
        body = (out / f"chapter-{n:03d}.body.md").read_text(encoding="utf-8")
        assert body == (out / f"chapter-{n:03d}.r2.draft.md").read_text(encoding="utf-8")
        row = load(out / f"chapters/{n:03d}.json")
        assert row["body_sha256"] == digest(body)
        stored = row["state"]["continuity_ledger"]["characters"]["顾寒"]["location"]
        assert stored["value"] == "门前" and stored["quote"] in body
        prepared = load(out / f"trial-inputs/{n:03d}.json")
        assert prepared.pop("sha256") == json_hash(prepared)
        assert row["writer_input_sha256"] == json_hash(prepared)
        if n > 1:
            assert row["previous_body_sha256"] == digest(
                (out / f"chapter-{n - 1:03d}.body.md").read_text(encoding="utf-8")
            )
        outline = load(out / f"writing_outlines/{n:03d}.json")
        assert outline["ending"] is False
    if source != BOOK:
        assert len((out / "chapter-003.body.md").read_text(encoding="utf-8")) > 8500
    writer = load(out / "inputs/001.json")
    assert "mock" not in pack(writer)
    assert writer["max_completion_tokens"] == 6500
    audited = [load(p) for p in (out / "inputs").glob("*.json")]
    facts = [r["prompt"] for r in audited if "-trial-state-" in r["stage"]]
    assert facts and all("归墟起手" not in p and "弃剑认输" not in p for p in facts)
    if arm == "A":
        assert "本章主场景硬合同" in writer["prompt"]
    else:
        assert all(cue not in writer["prompt"] for cue in ("本章主场景硬合同", "归墟起手", "弃剑认输", "只写两场戏"))
        assert all("只写两场戏" not in r["prompt"] for r in audited if r["stage"].endswith("-draft"))
        if source != BOOK:
            occurrence = [
                r["stage"] for r in audited if r["stage"].endswith("-draft") and "押印的收条交给掌柜" in r["prompt"]
            ]
            assert occurrence == ["r2-ch2-draft"]
            assert all("旧卷误写" not in r["prompt"] for r in audited if r["stage"].endswith("-draft"))
        if source == BOOK:
            first_local = load(out / "writing_outlines/001.json")["local_brief"]
            fourth_local = load(out / "writing_outlines/004.json")["local_brief"]
            assert "碎冰式" in pack(first_local) and "扫帚声" in pack(fourth_local)
            assert "弃剑" not in pack(fourth_local)
        assert "第3个字" in pack(load(out / "trial-inputs/004.json")["sections"])
    assert len(list((out / "inputs").glob("*.json"))) == len(load(out / "calls.json"))
    save(out / "chapter-001.body.md", "错误的另一章")
    with pytest.raises(ValueError, match="draft chain changed"):
        run_trial(source, out, None, arm)


def test_output_cannot_use_source_or_library():
    with pytest.raises(ValueError, match="separate private"):
        run_trial(BOOK, BOOK / "fiction-trial", None, "A")
