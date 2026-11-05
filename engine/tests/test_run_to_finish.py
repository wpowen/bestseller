"""Production client regressions; fake transport and clocks only."""

import io
import json
from pathlib import Path
from unittest.mock import patch
import urllib.error

import pytest

from bookengine.client import Client
from bookengine.retries import next_network_wait


def client(tmp_path):
    with patch("bookengine.client.credentials", return_value=("https://fake.invalid/v1", "fake-key")):
        return Client(tmp_path, tmp_path / "unused.env", "")


class Reply:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return json.dumps(self.value).encode()


def good(text="正文已完成。"):
    return Reply(
        dict(
            choices=[dict(message=dict(content=text), finish_reason="stop")],
            usage=dict(prompt_tokens=10, completion_tokens=10, total_tokens=20),
        )
    )


def test_network_wait_has_no_total_deadline_and_caps_each_wait():
    assert next_network_wait(20, 100000, 2000) == 600


def test_twenty_429_then_success_preserves_stage_and_usage(tmp_path):
    c = client(tmp_path)
    errors = [
        urllib.error.HTTPError("https://fake.invalid", 429, "rate limit", {}, io.BytesIO(b"{}")) for _ in range(20)
    ]
    waits = []

    def wait(name, state, path):
        if "delay" in state:
            waits.append(state.pop("delay"))
            state["waited"] += waits[-1]
            state.pop("not_before", None)

    with patch("urllib.request.urlopen", side_effect=[*errors, good()]), patch.object(c, "wait_network", wait):
        assert c.stage("chapter-1", "写正文") == "正文已完成。"
    assert len(c.calls) == 21
    assert len(waits) == 20 and max(waits) <= 600
    assert c.totals()["total_tokens"] == 20
    saved = (tmp_path / "steps/chapter-1.md").read_bytes()
    with patch("urllib.request.urlopen", side_effect=AssertionError("saved stage must not send")):
        assert c.stage("chapter-1", "写正文") == saved.decode()
    assert (tmp_path / "steps/chapter-1.md").read_bytes() == saved


def test_five_bad_outputs_then_success_even_when_callsite_requests_one(tmp_path):
    c = client(tmp_path)
    c.relaxed = True  # Old serial mode must not silently adopt empty responses.
    with patch("urllib.request.urlopen", side_effect=[good("")] * 5 + [good('{"ok":true}')]) as http:
        assert c.stage("plan", "给JSON", '{"ok":true}', max_attempts=1) == {"ok": True}
    assert http.call_count == 6
    prompts = [json.loads(call.args[0].data)["messages"][1]["content"] for call in http.call_args_list]
    assert all("empty_return" in p for p in prompts[1:])


def test_quota_is_distinct_and_can_resume_without_rewriting_saved_stage(tmp_path):
    from tools.runtime_support import QuotaExhausted

    c = client(tmp_path)
    with patch("urllib.request.urlopen", return_value=good()):
        c.stage("chapter-1", "写正文")
    saved = (tmp_path / "steps/chapter-1.md").read_bytes()
    with patch(
        "urllib.request.urlopen",
        return_value=Reply({"base_resp": {"status_code": 1008, "status_msg": "insufficient balance"}}),
    ):
        with pytest.raises(QuotaExhausted):
            c.stage("chapter-2", "写正文")
    assert json.loads((tmp_path / "runtime.json").read_text())["kind"] == "quota_exhausted"
    c = client(tmp_path)
    with patch("urllib.request.urlopen", return_value=good("第二章完成。")):
        assert c.stage("chapter-2", "写正文") == "第二章完成。"
    assert (tmp_path / "steps/chapter-1.md").read_bytes() == saved
    assert len(c.calls) == 3


@pytest.mark.parametrize(
    "code,expected",
    [
        (1008, "quota"),
        (2056, "quota"),
        (2067, "quota"),
        (1002, "waiting"),
        (2064, "waiting"),
        (1039, "error"),
        (2049, "error"),
    ],
)
def test_official_error_classification(code, expected):
    from tools.runtime_support import classify_api_error

    assert classify_api_error(code, "") == expected


def test_qclient_does_not_enforce_legacy_money_env(tmp_path, monkeypatch):
    from bookengine.q1 import QClient

    monkeypatch.setenv("BOOKENGINE_BUDGET_USD", "0")
    with patch("bookengine.client.credentials", return_value=("https://fake.invalid/v1", "fake-key")):
        c = QClient(tmp_path, tmp_path / "unused.env", "")
    with patch("urllib.request.urlopen", return_value=good()):
        assert c.stage("draft", "写正文")


def test_exhausted_bad_output_is_project_error_with_stage(tmp_path):
    from tools.runtime_support import ProjectError

    c = client(tmp_path)
    with patch("urllib.request.urlopen", return_value=good("")) as http:
        with pytest.raises(ProjectError, match="broken-plan"):
            c.stage("broken-plan", "给JSON", "{}", max_attempts=1)
    assert http.call_count == 6
    state = json.loads((tmp_path / "runtime.json").read_text())
    assert state["kind"] == "error" and state["stage"] == "broken-plan"


@pytest.mark.parametrize(
    "reply",
    [
        {"base_resp": {"status_code": 1002, "status_msg": "rate limit"}},
        {"error": {"message": "rate limit (1002)", "type": "rate_limit_error"}},
    ],
)
def test_twenty_business_throttles_are_waits_not_bad_outputs(tmp_path, reply):
    c = client(tmp_path)
    with (
        patch("urllib.request.urlopen", side_effect=[Reply(reply) for _ in range(20)] + [good()]),
        patch.object(c, "wait_network"),
    ):
        assert c.stage("body", "正文") == "正文已完成。"
    assert len(c.calls) == 21
    assert all(row["status"] == "provider_wait" for row in c.calls[:-1])


@pytest.mark.parametrize(
    "status,code,kind",
    [(429, 1008, "quota_exhausted"), (400, 2056, "quota_exhausted"), (401, 2049, "error"), (400, 1039, "error")],
)
def test_http_envelope_has_account_and_project_classification(tmp_path, status, code, kind):
    from tools.runtime_support import QuotaExhausted, ProjectError

    c = client(tmp_path)
    body = json.dumps({"error": {"message": f"provider error ({code})"}}).encode()
    error = urllib.error.HTTPError("https://fake.invalid", status, "error", {}, io.BytesIO(body))
    with patch("urllib.request.urlopen", side_effect=error):
        with pytest.raises(QuotaExhausted if kind == "quota_exhausted" else ProjectError):
            c.stage("body", "正文")
    assert json.loads((tmp_path / "runtime.json").read_text())["kind"] == kind


def q1_fixture(tmp_path, monkeypatch, chapters=2):
    from copy import deepcopy
    from bookengine import q1, q1_plan
    from bookengine.config import Book
    from bookengine.storage import save

    c = client(tmp_path)
    book = Book("suspense-mystery", chapters, 200, "离线假客户端")
    config = tmp_path / "book.yaml"
    save(config, book.record())
    bible = dict(
        title="离线续写",
        characters=[dict(name="阿宁", role="protagonist")],
        canon=deepcopy(q1_plan.GROWTH["packs"][book.prompt_pack]),
    )
    bible["canon"]["hook_types"] = list(q1.PLAN_DATA["hook_types"])
    monkeypatch.setattr(q1, "QClient", lambda *args: c)
    monkeypatch.setattr(q1, "prepare", lambda *args, **kw: (bible, {"threads": []}, {}))
    monkeypatch.setattr(q1, "volume", lambda *args, **kw: dict(number=1, start=1, end=chapters))
    monkeypatch.setattr(q1, "outlines", lambda *args, **kw: [dict(number=n) for n in range(1, chapters + 1)])
    extracted = dict(
        summary="阿宁推门回家。",
        state={},
        edits=[],
        events=[],
        advances=[],
        type="日常",
        hook_type="完结收束",
        opens_new_mainline=False,
        payoff=dict(type="无兑现", fulfilled=False, quote=""),
    )
    draft = "“我先回家，你在这里等我。”阿宁挥挥手。\n\n她推开院门，灯还亮着。"
    return q1, c, config, draft, json.dumps(extracted, ensure_ascii=False)


def test_whole_q1_book_finishes_after_twenty_429_with_quality_warnings(tmp_path, monkeypatch):
    q1, c, config, draft, extracted = q1_fixture(tmp_path, monkeypatch)
    errors = [
        urllib.error.HTTPError("https://fake.invalid", 429, "rate limit", {}, io.BytesIO(b"{}")) for _ in range(20)
    ]
    with (
        patch(
            "urllib.request.urlopen", side_effect=[*errors, good(draft), good(extracted), good(draft), good(extracted)]
        ),
        patch.object(c, "wait_network"),
    ):
        result = q1.run_q1(config, tmp_path, tmp_path / "unused.env", tmp_path)
    assert result["status"] == "complete" and result["completed_chapters"] == 2
    assert result["quality"]["word_band_pass"] == 0  # Quality warnings do not stop the book.
    assert (tmp_path / "book.docx").exists() and (tmp_path / "book.md").exists()
    assert len(c.calls) == 24


def test_whole_q1_quota_then_resume_preserves_completed_prose_bytes(tmp_path, monkeypatch):
    from tools.runtime_support import QuotaExhausted

    q1, c, config, draft, extracted = q1_fixture(tmp_path, monkeypatch)
    quota = Reply({"base_resp": {"status_code": 1008, "status_msg": "insufficient balance"}})
    with patch("urllib.request.urlopen", side_effect=[good(draft), good(extracted), quota]):
        with pytest.raises(QuotaExhausted):
            q1.run_q1(config, tmp_path, tmp_path / "unused.env", tmp_path)
    saved = (tmp_path / "chapter-001.body.md").read_bytes()
    row = (tmp_path / "chapters/001.json").read_bytes()
    assert json.loads((tmp_path / "run.json").read_text())["status"] == "quota_exhausted"
    with patch("urllib.request.urlopen", side_effect=[good(draft), good(extracted)]):
        result = q1.run_q1(config, tmp_path, tmp_path / "unused.env", tmp_path)
    assert result["status"] == "complete" and result["completed_chapters"] == 2
    assert (tmp_path / "chapter-001.body.md").read_bytes() == saved
    assert (tmp_path / "chapters/001.json").read_bytes() == row


def test_invalid_old_stage_is_retained_and_repaired_without_overwrite(tmp_path):
    from bookengine.storage import save

    target = tmp_path / "steps/plan.json"
    save(target, "broken: [")
    before = target.read_bytes()
    c = client(tmp_path)
    c.relaxed = True
    with patch("urllib.request.urlopen", return_value=good('{"ok":true}')) as http:
        assert c.stage("plan", "给JSON", '{"ok":true}') == {"ok": True}
    assert http.call_count == 1
    assert target.read_bytes() == before
    with patch("urllib.request.urlopen", side_effect=AssertionError("repaired step must be reused")):
        assert c.stage("plan", "给JSON", '{"ok":true}') == {"ok": True}
    assert target.read_bytes() == before


def test_false_validation_gets_six_attempts_before_project_error(tmp_path):
    from tools.runtime_support import ProjectError

    c = client(tmp_path)
    with patch("urllib.request.urlopen", return_value=good('{"ok":false}')) as http:
        with pytest.raises(ProjectError):
            c.stage("outline", "给JSON", "{}", validate=lambda value: value["ok"])
    assert http.call_count == 6
    assert not (tmp_path / "steps/outline.json").exists()


def test_concept_card_structure_gets_six_attempts_before_project_error(tmp_path):
    from bookengine.benchmarking import concept_design
    from bookengine.config import Book
    from tools.runtime_support import ProjectError

    c = client(tmp_path)
    with patch("urllib.request.urlopen", return_value=good('{"concepts":[]}')) as http:
        with pytest.raises(ProjectError, match="concept-12"):
            concept_design(c, Book("suspense-mystery", 2, 200, "假客户端"))
    assert http.call_count == 6
    assert not (tmp_path / "steps/concept-12.json").exists()


def test_cli_service_shutdown_during_wait_is_not_project_error(tmp_path, monkeypatch):
    from bookengine import __main__ as cli, q1

    monkeypatch.setattr("sys.argv", ["bookengine", "fake.yaml", "--q1", "--env", "unused.env", "--out", str(tmp_path)])
    monkeypatch.setattr(q1, "run_q1", lambda *args: (_ for _ in ()).throw(InterruptedError()))
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 130
    assert json.loads((tmp_path / "runtime.json").read_text())["kind"] == "interrupted"
