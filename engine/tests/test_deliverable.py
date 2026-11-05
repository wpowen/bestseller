"""Selected-card and early-card delivery contracts, with fake transports only."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
from unittest.mock import patch

import pytest
import yaml

from bookengine.config import Book
from bookengine.q1_prepare import prepare
from test_identity import ScriptedClient, bible, ROOT
from test_ideation import card
from ideation import run, save, read


@pytest.mark.parametrize(
    "name,identity,setting,obj",
    [
        ("苏小满", "修伞匠", "码头", "旧伞"),
        ("赵溪", "糕点师", "山城", "铜模"),
    ],
)
def test_selected_story_survives_tournament_renaming_and_resume(tmp_path, name, identity, setting, obj):
    selected = card(
        title="雨停之前",
        premise=f"{setting}{identity}{name}想救姐姐。",
        opening=f"{name}在{setting}撑开{obj}。",
        golden_finger_rule=f"{obj}只能挡一次雨。",
    )
    facts = dict(
        name=name,
        identity=identity,
        setting=setting,
        object=obj,
        keywords=dict(identity=[identity], setting=[setting], object=[obj], rule=["只能", "一次雨"]),
    )
    wrong = dict(bible=bible(), threads=[])
    responses = [json.dumps(facts, ensure_ascii=False)] + [json.dumps(wrong, ensure_ascii=False)] * 4
    client = ScriptedClient(tmp_path, responses)
    book = Book("xuanhuan-power-fantasy", 5, 2200, json.dumps(selected, ensure_ascii=False))
    value, _, _ = prepare(client, book, tmp_path, ROOT / "config", design=True)
    assert value["title"] == selected["title"]
    hero = next(c for c in value["characters"] if c["role"] == "protagonist")
    assert hero["name"] == name
    assert hero["identity"] == identity
    assert value["opening"] == selected["opening"]
    assert value["power"]["rule"] == selected["golden_finger_rule"]
    assert value["world"]["setting"] == setting
    assert value["selected_card"] == selected
    report = read(tmp_path / "card-alignment.json")
    assert len(report["attempts"]) == 4  # initial plus at most three regenerations
    assert report["corrected"]
    assert not any(c["stage"] == "concept-12" for c in client.calls)
    before = (tmp_path / "story_bible.yaml").read_bytes()
    prepare(ScriptedClient(tmp_path, []), book, tmp_path, ROOT / "config", design=True)
    assert (tmp_path / "story_bible.yaml").read_bytes() == before
    # A missing second checkpoint must not cause new calls or rewrite saved design.
    (tmp_path / "threads.yaml").unlink()
    prepare(ScriptedClient(tmp_path, []), book, tmp_path, ROOT / "config", design=True)
    assert (tmp_path / "story_bible.yaml").read_bytes() == before


def test_cards_are_selectable_while_judge_waits_and_indices_stay_stable(tmp_path):
    import ideation

    judging, release = threading.Event(), threading.Event()

    def fake_judge(folder, cards, *args, **kwargs):
        judging.set()
        assert release.wait(5)
        return [dict(c, scores={k: i for k in ideation.SCORES}) for i, c in enumerate(cards)]

    class Client:
        def __call__(self, messages, stage, **opts):
            n = int(stage[-1])
            return json.dumps(
                [
                    card(title=t + str(n), hook=t + str(n))
                    for t in ["大江？", "山海，归来", "旧巷故人", "荒原？", "长风，入城", "炊烟小镇"]
                ]
            )

        def estimate(self):
            return 0.01

    save(tmp_path / "topic.json", dict(status="running", genre="suspense-mystery", chapters=5, idea=""))
    market = dict(status="missing", date="2026-11-05", books=[])
    with (
        patch("ideation.snapshot", return_value=market),
        patch("ideation.features", return_value={}),
        patch("ideation.judge", side_effect=fake_judge),
        ThreadPoolExecutor(1) as pool,
    ):
        future = pool.submit(run, tmp_path, Client(), tmp_path)
        try:
            assert judging.wait(3)
            early = read(tmp_path / "topic.json")
            assert early["status"] == "ready"
            assert early["candidates"]
            early["book_id"] = "webui/selected-while-ranking"
            save(tmp_path / "topic.json", early)
        finally:
            release.set()
        final = future.result()
    assert final["book_id"] == early["book_id"]
    assert [c["title"] for c in early["candidates"]] == [c["title"] for c in final["candidates"]]
    assert final["status"] == "complete"


def test_fallback_removes_conflicting_mechanism_and_resolves_name_collision():
    from bookengine.selected_card import enforce, differences
    from bookengine.q1_schema import defaults, DATA
    from bookengine.identity import validate_bible

    selected = card(
        title="旧伞渡口",
        premise="修伞匠赵溪想找姐姐。",
        opening="赵溪在码头撑开旧伞。",
        golden_finger_rule="旧伞只能挡一次雨。",
    )
    locked = dict(
        name="赵溪",
        identity="修伞匠",
        setting="码头",
        object="旧伞",
        keywords=dict(identity=["修伞匠"], setting=["码头"], object=["旧伞"], rule=["一次雨"]),
    )
    wrong = defaults(bible(), DATA["schemas"]["prepare"]["bible"])
    wrong["characters"][1]["name"] = "赵溪"
    wrong["world"] = dict(rules=["博物馆禁止出门"], levels=[])
    wrong["power"] = dict(ability="敲钟复原", cost="失去声音", limit="每天三次")
    fixed, _ = enforce(wrong, {"threads": []}, selected, locked, ["name", "rule", "setting"])
    validate_bible(fixed)
    assert differences(fixed, selected, locked) == []
    assert not any(
        word in json.dumps(fixed, ensure_ascii=False) for word in ["博物馆", "敲钟复原", "失去声音", "每天三次"]
    )


def test_first_group_is_selectable_before_slowest_generation_finishes(tmp_path):
    finished, release = threading.Event(), threading.Event()

    class Client:
        def __call__(self, messages, stage, **opts):
            if stage == "generate-3":
                finished.set()
                assert release.wait(4)
            if stage.startswith("judge"):
                return "broken"
            return json.dumps([card(title="纸上故人", hook="她在雨里等回一封迟到三年的家书")])

        def estimate(self):
            return 0.01

    save(tmp_path / "topic.json", dict(status="running", genre="suspense-mystery", chapters=5, idea=""))
    market = dict(status="missing", date="2026-11-05", books=[])
    import time

    with (
        patch("ideation.snapshot", return_value=market),
        patch("ideation.features", return_value={}),
        ThreadPoolExecutor(1) as pool,
    ):
        future = pool.submit(run, tmp_path, Client(), tmp_path)
        try:
            assert finished.wait(2)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and read(tmp_path / "topic.json")["status"] != "ready":
                time.sleep(0.01)
            early = read(tmp_path / "topic.json")
            assert early["status"] == "ready"
            assert early["candidates"]
            assert not future.done()
        finally:
            release.set()
        assert future.result()["candidates"][0]["title"] == early["candidates"][0]["title"]
