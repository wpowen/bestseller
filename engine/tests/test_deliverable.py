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


def core_development_response():
    return json.dumps(
        dict(
            volumes=[
                dict(
                    number=1,
                    core_development=dict(
                        new_use="借旧伞撑开一次封雨通道", new_cost="忘记亲人的一种笑容", new_limit="必须在真正雨中触发"
                    ),
                )
            ]
        ),
        ensure_ascii=False,
    )


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
    responses = [json.dumps(facts, ensure_ascii=False)] + [json.dumps(wrong, ensure_ascii=False)] * 7
    responses += [
        json.dumps(
            dict(
                cost="损失：失去童年一次闻花香的记忆；心理：记忆缺口使他不敢认妹妹送的花，反复向她道歉；等级：1；升级：首次付出记忆代价"
            ),
            ensure_ascii=False,
        )
    ]
    responses.append(core_development_response())
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


def test_unnamed_card_recovery_uses_the_same_assigned_cast(tmp_path):
    selected = card(
        premise="码头修伞匠想找回姐姐。", opening="修伞匠在码头撑开旧伞。", golden_finger_rule="旧伞只能挡一次雨。"
    )
    locked = dict(
        name="",
        identity="修伞匠",
        setting="码头",
        object="旧伞",
        keywords=dict(identity=["修伞匠"], setting=["码头"], object=["旧伞"], rule=["一次雨"]),
    )
    wrong = dict(
        bible=bible(),
        threads={"threads": [dict(id="main", kind="main", beats=[dict(volume=1, description="陆惊山救姐姐")])]},
    )
    client = ScriptedClient(
        tmp_path,
        [json.dumps(locked, ensure_ascii=False)]
        + [json.dumps(wrong, ensure_ascii=False)] * 7
        + [
            json.dumps(
                dict(
                    cost="损失：失去童年一次闻花香的记忆；心理：记忆缺口使他不敢认妹妹送的花，反复向她道歉；等级：1；升级：首次付出记忆代价"
                ),
                ensure_ascii=False,
            )
        ]
        + [core_development_response()],
    )
    book = Book("xuanhuan-power-fantasy", 5, 2200, json.dumps(selected, ensure_ascii=False))
    saved_bible, saved_threads, _ = prepare(client, book, tmp_path, ROOT / "config")
    (tmp_path / "threads.yaml").unlink()
    recovered_bible, recovered_threads, _ = prepare(ScriptedClient(tmp_path, []), book, tmp_path, ROOT / "config")
    assert recovered_bible == saved_bible
    assert recovered_threads == saved_threads


def test_selected_facts_allow_whitespace_changes_in_copied_keywords(tmp_path):
    from bookengine.selected_card import facts

    selected = card(premise="糕点师沈禾在老街找哥哥。", golden_finger_rule="铜模只能复原 一种糕点，代价是忘记童年。")
    expected = dict(
        name="沈禾",
        identity="糕点师",
        setting="老街",
        object="铜模",
        keywords=dict(identity=["糕点师"], setting=["老街"], object=["铜模"], rule=["复原一种糕点", "忘记童年"]),
    )
    client = ScriptedClient(tmp_path, [json.dumps(expected, ensure_ascii=False)])
    assert facts(client, selected) == expected
    assert len(client.calls) == 1


def test_unspecified_legacy_card_facts_are_not_invented(tmp_path):
    from bookengine.selected_card import facts

    selected = dict(title="一家人", premise="她想让姐姐回家。")
    expected = dict(
        name="", identity="", setting="", object="", keywords={k: [] for k in ["identity", "setting", "object", "rule"]}
    )
    assert facts(ScriptedClient(tmp_path, [json.dumps(expected)]), selected) == expected


def test_late_group_is_deduplicated_against_already_published_cards(tmp_path):
    import ideation

    published = threading.Event()
    original_update = ideation.update_topic

    def update(folder, **patch):
        result = original_update(folder, **patch)
        if result.get("status") == "ready" and result.get("candidates"):
            published.set()
        return result

    class Client:
        def __call__(self, messages, stage, **opts):
            if stage.startswith("judge"):
                return "bad"
            n = int(stage[-1])
            if n == 0:
                assert published.wait(3)
                return json.dumps([card(title="荒村雨夜来客又归来", hook="她用铜钟换回姐姐的一条命")])
            if n == 1:
                return json.dumps([card(title="荒村雨夜来客", hook="她用铜钟换回姐姐的一条命")])
            return "[{}]"

        def estimate(self):
            return 0.01

    save(tmp_path / "topic.json", dict(status="running", genre="suspense-mystery", chapters=5, idea=""))
    market = dict(status="missing", date="2026-11-05", books=[])
    with (
        patch("ideation.snapshot", return_value=market),
        patch("ideation.features", return_value={}),
        patch("ideation.update_topic", side_effect=update),
    ):
        result = run(tmp_path, Client(), tmp_path)
    assert [c["title"] for c in result["candidates"]] == ["荒村雨夜来客"]
    assert "荒村雨夜来客" not in [c["title"] for c in result["rejected"]]
    assert "荒村雨夜来客又归来" in [c["title"] for c in result["rejected"]]
