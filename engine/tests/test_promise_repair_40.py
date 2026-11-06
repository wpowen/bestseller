"""Saved responses and bounded, exact-source promise repair without network."""

import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from bookengine import q1_promise_repair as module
from bookengine.client import content_of
from bookengine.opening_promises import review_opening
from bookengine.promise_io import precise_ask
from bookengine.text import parse_json

FIXTURE = json.loads((Path(__file__).parent / "fixtures/promise_repair40/trial50.json").read_text())


def drive(monkeypatch, ask, text="林宁握钟。\n\n林宁守在石桥。", extract=None, bible=None):
    before = dict(status="unfulfilled", items=[dict(id="core-1", kind="core_use", status="missing", paragraphs=[1])])
    monkeypatch.setattr(
        module,
        "review_opening",
        lambda client, key, *args: dict(status="checked", items=[]) if "recheck" in key else before,
    )
    return module.repair(
        None,
        "ch1",
        text,
        {},
        True,
        {},
        bible or {},
        {"number": 1},
        {},
        extract or (lambda *a, **k: ({"events": [], "summary": "新稿"}, False, [])),
        ask,
    )


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda c: f"saved-ch{c['chapter']}")
def test_all_saved_rollbacks_reach_repair_with_exact_targets(monkeypatch, case):
    # Freeze the saved review so other contract fixes cannot change the input.
    monkeypatch.setattr(module, "review_opening", lambda *a: copy.deepcopy(case["before"]))
    observed = []

    def ask(client, key, instruction, sections, schema, **kwargs):
        observed.append(key)
        assert client.prefix == "" and client.clip_context is False
        targets = json.loads(dict(sections)["允许修改的段"])
        spans = list(re.finditer(r"\S[\s\S]*?(?=\n\s*\n|$)", case["body"]))
        assert targets
        for row in targets:
            assert row["original"] == spans[row["id"] - 1].group()
        return {"edits": []}

    client = SimpleNamespace(prefix="原前缀", context_limit=10000, clip_context=True)
    result = module.repair(
        client,
        f"r2-ch{case['chapter']}",
        case["body"],
        {},
        True,
        {},
        FIXTURE["bible"],
        {"number": case["chapter"]},
        {},
        lambda *a, **k: pytest.fail("empty edits cannot extract"),
        ask,
    )
    assert observed, "the old full-bible payload blocks the request before the client"
    assert len(observed) == 2
    assert result[:3] == (case["body"], {}, True)
    assert [a["reason"] for a in result[-1]["attempts"]] == ["empty_edits", "empty_edits"]
    assert client.prefix == "原前缀" and client.clip_context is True


def test_saved_response_inventory_has_no_repair_response():
    assert FIXTURE["repair_calls"] == []
    assert len(FIXTURE["cases"]) == 15 and len(FIXTURE["responses"]) == 28
    assert all("promise-review" in r["stage"] for r in FIXTURE["responses"])
    assert all(content_of(r["raw"]) for r in FIXTURE["responses"])


@pytest.mark.parametrize("case", FIXTURE["cases"][:3], ids=lambda c: f"review-ch{c['chapter']}")
def test_replay_saved_review_response_text(case):
    responses = {}
    for row in FIXTURE["responses"]:
        responses[row["stage"]] = row["raw"]

    def ask(client, key, *args, **kwargs):
        return parse_json(content_of(responses[key]))

    earlier = {c["chapter"]: c["body"] for c in FIXTURE["cases"] if c["chapter"] < case["chapter"]}
    note = review_opening(
        None, f"r2-ch{case['chapter']}", case["body"], FIXTURE["bible"], case["chapter"], earlier, ask
    )
    assert note["status"] == "unfulfilled"
    assert any(i["status"] in ("unknown", "missing") for i in note["items"])


@pytest.mark.parametrize(
    "response,reason",
    [
        (None, "invalid_edit_schema"),
        ({"edits": "bad"}, "invalid_edit_schema"),
        ({"edits": []}, "empty_edits"),
        ({"edits": [{"id": True, "original": "林宁握钟。", "text": "新稿。"}]}, "invalid_target_id"),
        ({"edits": [{"id": 99, "original": "林宁握钟。", "text": "新稿。"}]}, "invalid_target_id"),
        ({"edits": [{"id": 1, "original": "林宁握钟。", "text": "  "}]}, "empty_replacement"),
        ({"edits": [{"id": 1, "original": "不存在。", "text": "新稿。"}]}, "original_mismatch"),
        ({"edits": [{"id": 1, "original": "林宁握钟。", "text": "林宁握钟。"}]}, "unchanged_edits"),
        ({"edits": [{"id": 1, "original": "林宁握钟。", "text": "一。\n\n二。"}]}, "multiple_paragraphs"),
        ({"edits": [{"id": 1, "original": "林宁握钟。", "text": "一。\n \n二。"}]}, "multiple_paragraphs"),
    ],
)
def test_bad_edits_retry_once_then_adopt_rechecked_candidate(monkeypatch, response, reason):
    seen = []
    replacement = "林宁拨动旧钟救下妹妹，失去了母亲的记忆。"

    def ask(client, key, instruction, sections, schema, **kwargs):
        seen.append((key, dict(sections)))
        if len(seen) == 1:
            return response
        assert reason in dict(sections)["上次失败"]
        return {"edits": [{"id": 1, "original": "林宁握钟。", "text": replacement}]}

    result = drive(monkeypatch, ask)
    assert result[:3] == (replacement + "\n\n林宁守在石桥。", {"events": [], "summary": "新稿"}, False)
    assert [r[0] for r in seen] == ["ch1-promise-repair", "ch1-promise-repair-retry-1"]
    assert result[-1]["attempts"][0]["reason"] == reason
    assert result[-1]["status"] == "repaired"


@pytest.mark.parametrize("error", ["context_unknown", "empty_edits", "original_mismatch"])
def test_failed_attempts_remain_distinguishable_and_preserve_original(monkeypatch, error):
    count = []

    def ask(*args, **kwargs):
        count.append(1)
        if error == "context_unknown":
            return {"context_unknown": {"reason": "complete_sources_exceed_context", "required": 12000, "limit": 10000}}
        if error == "original_mismatch":
            return {"edits": [{"id": 1, "original": "伪造", "text": "新稿"}]}
        return {"edits": []}

    result = drive(monkeypatch, ask)
    assert len(count) == 2 and result[:3] == ("林宁握钟。\n\n林宁守在石桥。", {}, True)
    note = result[-1]
    assert note["status"] == "rolled_back" and note["reason"] == "invalid_or_empty_targeted_edits"
    assert [a["reason"] for a in note["attempts"]] == [error, error]
    if error == "context_unknown":
        assert note["attempts"][0]["context_unknown"]["required"] == 12000


def test_repair_context_guard_is_reported_without_client_call(monkeypatch):
    result = drive(
        monkeypatch, lambda *a, **k: pytest.fail("oversized sources"), bible={"power": {"rule": "字" * 15000}}
    )
    assert result[0] == "林宁握钟。\n\n林宁守在石桥。"
    assert all(a["reason"] == "context_unknown" for a in result[-1]["attempts"])


@pytest.mark.parametrize("invalid", [True, False])
def test_final_state_and_repetition_guards_still_rollback_after_retry(monkeypatch, invalid):
    calls = []
    closing = "林宁一直守在横跨山谷的古老石桥上。"
    original = "林宁握钟。\n\n" + closing

    def ask(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return {"edits": []}
        replacement = "林宁拨动旧钟救下妹妹。" if invalid else closing
        return {"edits": [{"id": 1, "original": "林宁握钟。", "text": replacement}]}

    result = drive(monkeypatch, ask, text=original, extract=lambda *a, **k: ({"events": []}, invalid, []))
    assert len(calls) == 2
    assert result[:3] == (original, {}, True)
    assert result[-1]["reason"] == "final_state_or_existing_guard_failed"


def test_legacy_full_sources_hit_context_guard_for_every_saved_case():
    sizes = []
    for case in FIXTURE["cases"]:
        response = precise_ask(
            lambda *a, **k: pytest.fail("old request must not be sent"),
            None,
            "legacy",
            "定向修复",
            [("只读圣经", json.dumps(FIXTURE["bible"], ensure_ascii=False)), ("只读正文", case["body"])],
            "{}",
        )
        sizes.append(response["context_unknown"]["required"])
    assert min(sizes) > 10000


def test_mixed_invalid_edits_are_atomic_and_do_not_expand_targets(monkeypatch):
    def ask(*args, **kwargs):
        return {
            "edits": [
                {"id": 1, "original": "林宁握钟。", "text": "林宁借钟退敌。"},
                {"id": 2, "original": "林宁守在石桥。", "text": "改掉不允许的结尾。"},
            ]
        }

    result = drive(monkeypatch, ask)
    assert result[:3] == ("林宁握钟。\n\n林宁守在石桥。", {}, True)
    assert all(a["reason"] == "invalid_target_id" and a["targets"] == [1] for a in result[-1]["attempts"])


def test_duplicate_ids_and_failed_promise_recheck_preserve_original(monkeypatch):
    edit = {"id": 1, "original": "林宁握钟。", "text": "林宁借钟退敌。"}
    result = drive(monkeypatch, lambda *a, **k: {"edits": [edit, edit]})
    assert result[:3] == ("林宁握钟。\n\n林宁守在石桥。", {}, True)
    assert all(a["reason"] == "invalid_target_id" for a in result[-1]["attempts"])

    before = dict(status="unfulfilled", items=[dict(id="core-1", kind="core_use", status="missing", paragraphs=[1])])
    monkeypatch.setattr(module, "review_opening", lambda *a: before)
    result = module.repair(
        None,
        "ch1",
        "林宁握钟。",
        {},
        True,
        {},
        {},
        {"number": 1},
        {},
        lambda *a, **k: pytest.fail("failed promise recheck must not extract"),
        lambda *a, **k: {"edits": [edit]},
    )
    assert result[:3] == ("林宁握钟。", {}, True)
    assert result[-1]["reason"] == "promise_recheck_failed"


@pytest.mark.parametrize("preexisting", [False, True])
def test_identity_guard_compares_old_and_new_with_same_bible(monkeypatch, preexisting):
    old = "林宁这个旧渡口最后一位守灯人关了门。" if preexisting else "林宁把旧钟抱在怀里。"
    replacement = "林宁是旧渡口最后一位守灯人，拨钟救下妹妹。"
    original = old + "\n\n林宁守在石桥。"
    bible = {"characters": [{"name": "林宁", "identity": "旧渡口最后一位守灯人"}]}
    result = drive(
        monkeypatch,
        lambda *a, **k: {"edits": [{"id": 1, "original": old, "text": replacement}]},
        text=original,
        bible=bible,
    )
    if preexisting:
        assert result[0] == replacement + "\n\n林宁守在石桥。"
        assert result[-1]["status"] == "repaired"
    else:
        assert result[:3] == (original, {}, True)
        assert result[-1]["reason"] == "final_state_or_existing_guard_failed"
