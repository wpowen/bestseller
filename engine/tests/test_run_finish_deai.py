"""Offline run-to-finish transport, recovery and quality rollback contracts."""

import io
import json
import threading
from types import SimpleNamespace
import urllib.error

import pytest

from deai import client as transport
from deai.flow import Session, polish
from deai.io import process, read_json, save


class Response:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def completion(content='{"ok":true}', usage=None):
    return Response(dict(choices=[dict(message=dict(content=content))], usage=usage))


@pytest.fixture
def http(tmp_path, monkeypatch):
    """No real HTTP, sleeps, credentials or production book paths."""
    env = tmp_path / ".env"
    env.write_text("MINIMAX_API_KEY=fixture-placeholder\n")
    book = tmp_path / "book"
    book.mkdir()
    work = book / "final/.deai/001"
    clock = [0.0]
    waiting = []

    def pause(seconds):
        if seconds > 0:
            waiting.append(read_json(book / "runtime.json", {}))
            clock[0] += seconds

    monkeypatch.setattr(transport, "pause", pause)
    monkeypatch.setattr(transport.time, "monotonic", lambda: clock[0])
    transport.STOP.clear()
    requests = []
    replies = []

    def open_request(request, **options):
        requests.append(json.loads(request.data))
        assert replies, "Unexpected HTTP request"
        reply = replies.pop(0)
        if callable(reply):
            reply = reply(requests[-1])
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(transport.urllib.request, "urlopen", open_request)
    yield SimpleNamespace(
        env=env, book=book, work=work, requests=requests, replies=replies, waiting=waiting, clock=clock
    )
    transport.STOP.clear()


def test_twenty_429s_finish_with_capped_unlimited_wait_and_ledger(http):
    http.replies.extend(
        urllib.error.HTTPError(
            "https://fixture.invalid", 429, "busy", {"Retry-After": "1200"}, io.BytesIO(b'{"error":"busy"}')
        )
        for _ in range(20)
    )
    http.replies.append(completion(usage=dict(prompt_tokens=100, completion_tokens=20)))
    client = transport.MiniMax(http.work, http.env)
    assert client([dict(role="user", content="fixture")], stage="detector") == '{"ok":true}'
    assert len(http.requests) == len(client.calls) == 21
    assert http.clock[0] > 900
    waits = [value for value in http.waiting if value.get("kind") == "waiting"]
    assert waits and max(value["retry"] for value in waits) == 20
    assert all(0 < value["next_retry_at"] - value["updated"] <= 600 for value in waits)
    state = read_json(http.book / "runtime.json")
    assert state["kind"] == "running"
    assert not state.get("next_retry_at")
    assert client.calls[-1]["usage"]["completion_tokens"] == 20
    assert client.estimate() > 0
    assert all(row["status"] == "error" for row in client.calls[:-1])


@pytest.mark.parametrize("code", [1008, 2053, 2056, 2067])
@pytest.mark.parametrize("http_status", [200, 429])
def test_quota_stops_distinctly_and_resumes_without_rewriting_receipts(http, code, http_status):
    payload = dict(base_resp=dict(status_code=code, status_msg="account exhausted"))
    if http_status == 200:
        http.replies.append(Response(payload))
    else:
        http.replies.append(
            urllib.error.HTTPError(
                "https://fixture.invalid", http_status, "quota", {}, io.BytesIO(json.dumps(payload).encode())
            )
        )
    client = transport.MiniMax(http.work, http.env)
    with pytest.raises(RuntimeError) as caught:
        client([], stage="facts")
    assert type(caught.value).__name__ == "QuotaExhausted"
    assert read_json(http.book / "runtime.json")["kind"] == "quota_exhausted"
    before = (http.work / "http/000000.raw").read_bytes()
    http.replies.append(completion())
    resumed = transport.MiniMax(http.work, http.env)
    assert resumed([], stage="facts") == '{"ok":true}'
    assert (http.work / "http/000000.raw").read_bytes() == before
    assert len(resumed.calls) == 2
    assert resumed.calls[0]["usage"] is None
    assert read_json(http.book / "runtime.json")["kind"] == "running"


@pytest.mark.parametrize("code", [1000, 1001, 1002, 1024, 1033, 1041, 1045, 2045, 2046, 2047, 2062, 2064, 2066, 2160])
def test_success_http_with_waiting_business_code_retries(http, code):
    http.replies.extend([Response(dict(base_resp=dict(status_code=code, status_msg="busy"))), completion()])
    assert transport.MiniMax(http.work, http.env)([], stage="candidate") == '{"ok":true}'
    assert len(http.requests) == 2
    assert any(row.get("kind") == "waiting" for row in http.waiting)


def test_max_tokens_error_is_project_error(http):
    http.replies.append(Response(dict(base_resp=dict(status_code=1039, status_msg="adjust max_tokens"))))
    with pytest.raises(RuntimeError) as caught:
        transport.MiniMax(http.work, http.env)([], stage="diagnostics")
    assert type(caught.value).__name__ != "QuotaExhausted"
    assert read_json(http.book / "runtime.json")["kind"] == "error"
    assert len(http.requests) == 1


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 599])
def test_unknown_business_code_does_not_override_http_waiting(http, status):
    payload = dict(base_resp=dict(status_code=9999, status_msg="unknown gateway error"))
    http.replies.extend(
        [
            urllib.error.HTTPError(
                "https://fixture.invalid", status, "busy", {}, io.BytesIO(json.dumps(payload).encode())
            ),
            completion(),
        ]
    )
    assert transport.MiniMax(http.work, http.env)([], stage="facts") == '{"ok":true}'
    assert len(http.requests) == 2


def test_transport_empty_responses_exhaust_as_format_project_error(http):
    http.replies.extend(completion(None) for _ in range(6))
    with pytest.raises(RuntimeError) as caught:
        transport.MiniMax(http.work, http.env)([], stage="facts")
    assert getattr(caught.value, "error_kind", "") == "format"
    assert len(http.requests) == 6


@pytest.mark.parametrize("failure", [urllib.error.URLError("offline"), TimeoutError("timeout")])
def test_connection_failure_has_no_total_wait_limit(http, failure):
    http.replies.extend([failure] * 20 + [completion()])
    assert transport.MiniMax(http.work, http.env)([], stage="recheck") == '{"ok":true}'
    assert len(http.requests) == 21
    assert http.clock[0] > 900


def test_old_money_environment_does_not_limit_and_unknown_usage_is_retained(http, monkeypatch):
    monkeypatch.setenv("DEAI_BUDGET_USD", "0")
    monkeypatch.setenv("DEAI_BUDGET_ROOT", "/nonexistent/unrelated")
    http.replies.append(completion(usage=dict(prompt_tokens=9000000)))
    client = transport.MiniMax(http.work, http.env)
    assert client([], stage="expensive") == '{"ok":true}'
    assert client.calls[0]["usage"] == dict(prompt_tokens=9000000)
    assert client.estimate() > 1
    assert not hasattr(client, "budget")
    assert not hasattr(client, "budget_root")


@pytest.mark.parametrize("bad", ["{truncated", "", None])
def test_five_bad_responses_then_success_include_previous_error(http, bad):
    http.replies.extend([completion(bad) for _ in range(5)] + [completion()])
    session = Session(transport.MiniMax(http.work, http.env), {})
    assert session.ask("facts", "Return JSON", {}) == dict(ok=True)
    assert len(http.requests) == 6
    for request in http.requests[1:]:
        assert "上次" in request["messages"][-1]["content"]
        assert any(word in request["messages"][-1]["content"] for word in ("completion", "JSON"))


def test_six_bad_responses_are_chapter_response_error_with_stage(http):
    http.replies.extend(completion("{broken") for _ in range(6))
    with pytest.raises(transport.ModelResponseError) as caught:
        Session(transport.MiniMax(http.work, http.env), {}).ask("facts", "Return JSON", {})
    assert "facts" in str(caught.value)
    assert len(http.requests) == 6
    state = read_json(http.book / "runtime.json")
    assert state["kind"] == "running"
    assert not transport.STOP.is_set()
    assert "facts" in state["stage"]
    assert getattr(caught.value, "error_kind", "") == "format"


@pytest.mark.parametrize(
    "stage,data,valid",
    [
        ("diagnostics", dict(paragraphs=[dict(pid="P1")]), dict(checked_pids=["P1"], facts=[], chapter={})),
        ("recall22-facts", dict(items=[dict(id="P1")]), dict(checks=[dict(id="P1", ok=False)])),
        (
            "recall22-candidates-0",
            dict(targets=[dict(sid="S1")]),
            dict(candidates=[dict(sid="S1", choices=["她把门推开。"])]),
        ),
        (
            "recall22-initial-verb_mismatch-0",
            dict(paragraphs=[dict(pid="P1", sentences=[dict(sid="S1")])], examples=[], requested_type="verb_mismatch"),
            dict(checked_pids=["P1"], issues=[]),
        ),
    ],
)
def test_valid_json_with_bad_stage_shape_also_gets_six_attempts(http, stage, data, valid):
    http.replies.extend([completion("{}") for _ in range(5)] + [completion(json.dumps(valid))])
    assert Session(transport.MiniMax(http.work, http.env), {}).ask(stage, "Return JSON", data) == valid
    assert len(http.requests) == 6
    assert all("JSON" in request["messages"][-1]["content"] for request in http.requests[1:])


def test_programming_error_is_not_a_quality_rollback(http, monkeypatch):
    from tools.runtime_support import ProjectError

    def broken(*args):
        raise TypeError("fixture bug")

    monkeypatch.setattr(Session, "classify", broken)
    with pytest.raises(ProjectError):
        polish("她把信放在门边。", {}, lambda *args: None)


def test_rollbacks_are_cached_but_never_reported_as_accepted(http):
    from deai.flow import Result

    source = http.book / "chapter-001.md"
    source.write_text("她把信放在门边。")
    client = lambda *args, **kw: SimpleNamespace(calls=[], estimate=lambda: 0)
    assert (
        process(
            http.book, "001", source, http.env, "local", lambda text, *args: Result(text, status="retained"), client
        )
        == "retained"
    )

    def unexpected(*args):
        raise AssertionError("Already processed chapter must not be charged again")

    assert process(http.book, "001", source, http.env, "local", unexpected, unexpected) == "cached"
    report = read_json(http.book / "final/chapter-001.deai.json")
    assert report["complete"] is False and report["processing_complete"] is True


def test_finished_http_checkpoint_is_reused_byte_for_byte(http):
    http.replies.append(completion())
    transport.MiniMax(http.work, http.env)([], stage="done")
    files = {path: path.read_bytes() for path in (http.work / "http").glob("*") if path.is_file()}
    assert transport.MiniMax(http.work, http.env)([], stage="done") == '{"ok":true}'
    assert len(http.requests) == 1
    assert all(path.read_bytes() == body for path, body in files.items())


def test_polish_does_not_swallow_quota_or_project_errors(http, monkeypatch):
    from tools.runtime_support import ProjectError, QuotaExhausted

    for error in (QuotaExhausted("fixture quota"), ProjectError("fixture broken stage")):
        monkeypatch.setattr(Session, "classify", lambda *args: (_ for _ in ()).throw(error))
        with pytest.raises(type(error)):
            polish("她推开了院门。", {}, lambda *args: None)


def test_quality_rollback_keeps_record_and_cli_continues(http, monkeypatch):
    from deai import __main__ as cli
    from deai.flow import Result

    for number in ("001", "002"):
        save(http.book, f"chapter-{number}.md", "她把信放在门边。")
    monkeypatch.setattr(cli, "MiniMax", lambda *args, **kw: SimpleNamespace(calls=[], estimate=lambda: 0))
    monkeypatch.setattr(
        "deai.polish", lambda text, *args: Result(text, status="retained", warnings=[dict(rollback="facts")])
    )
    assert cli.main(["run", str(http.book), "--env", str(http.env)]) == 0
    for number in ("001", "002"):
        report = read_json(http.book / f"final/chapter-{number}.deai.json")
        assert report["warnings"] == [dict(rollback="facts")]
        assert report["status"] == "retained"
        assert report["complete"] is False
        assert (
            (http.book / f"chapter-{number}.md").read_bytes()
            == b"\xe5\xa5\xb9\xe6\x8a\x8a\xe4\xbf\xa1\xe6\x94\xbe\xe5\x9c\xa8\xe9\x97\xa8\xe8\xbe\xb9\xe3\x80\x82"
        )


def test_cli_help_has_no_money_options(capsys):
    from deai.__main__ import main

    with pytest.raises(SystemExit) as caught:
        main(["run", "--help"])
    assert caught.value.code == 0
    assert "--budget" not in capsys.readouterr().out


def test_process_failure_preserves_completed_output_and_checkpoint(http):
    from tools.runtime_support import ProjectError

    source = http.book / "chapter-001.md"
    source.write_text("她把信放在门边。")
    save(http.book, "final/chapter-001.md", "她把信放到了门边。")
    save(http.book, "final/chapter-001.deai.json", dict(complete=True, status="polished"))
    save(http.work, "input.md", source.read_text())
    save(http.work, "output.md", "她把信放到了门边。")
    save(http.work, "policy.json", dict(version="old"))
    original = {path: path.read_bytes() for path in http.book.rglob("*") if path.is_file()}

    def fail(*args):
        raise ProjectError("fixture project failure")

    with pytest.raises(ProjectError):
        process(http.book, "001", source, http.env, "local", fail, lambda *args, **kwargs: SimpleNamespace())
    assert all(path.read_bytes() == body for path, body in original.items())
    assert (http.work / "attempt-last.json").exists()


def test_cli_terminal_quota_is_not_erased_by_a_finishing_sibling(http, monkeypatch):
    from deai import __main__ as cli
    from tools.runtime_support import QuotaExhausted, write_runtime

    ready = threading.Event()
    quota = threading.Event()
    for number in ("001", "002"):
        save(http.book, f"chapter-{number}.md", "她把信放在门边。")

    def chapter(book, number, *args):
        if number == "001":
            assert ready.wait(2)
            write_runtime(book, "quota_exhausted", "fixture-facts", error="fixture quota")
            quota.set()
            raise QuotaExhausted("fixture quota")
        ready.set()
        assert quota.wait(2)
        write_runtime(book, "running", "fixture-done")
        return "unchanged"

    monkeypatch.setattr(cli, "process", chapter)
    with pytest.raises(QuotaExhausted):
        cli.main(["run", str(http.book), "--env", str(http.env), "--jobs", "2"])
    state = read_json(http.book / "runtime.json")
    assert state["kind"] == "quota_exhausted"


def test_actual_cli_polish_with_fake_http_rolls_back_and_caches_two_chapters(http):
    from deai.__main__ import main

    text = "他沉声道：“我去找她。”\n\n她把信放在门边，又拉开窗帘往外看，院子里的人还站在雨棚下面等着。"
    for number in ("001", "002"):
        save(http.book, f"chapter-{number}.md", text)

    def respond(request):
        data = next(
            json.loads(message["content"].split("\n")[-1])
            for message in reversed(request["messages"])
            if message["content"].split("\n")[-1].startswith("{")
        )
        if "requested_type" in data:
            value = dict(checked_pids=[p["pid"] for p in data["paragraphs"]], issues=[])
        elif "targets" in data:
            value = dict(candidates=[dict(sid=t["sid"], choices=[t["text"]]) for t in data["targets"]])
        elif "items" in data:
            value = dict(checks=[dict(id=item["id"], ok=False, reason="fixture rollback") for item in data["items"]])
        else:
            value = dict(checked_pids=[p["pid"] for p in data["paragraphs"]], facts=[], chapter={})
        return completion(json.dumps(value), usage=dict(prompt_tokens=100, completion_tokens=50))

    http.replies.extend([respond] * 200)
    assert main(["run", str(http.book), "--env", str(http.env)]) == 0
    for number in ("001", "002"):
        report = read_json(http.book / f"final/chapter-{number}.deai.json")
        assert report["warnings"] and report["warnings"][0]["rollback"] == "facts"
        assert report["cumulative"]["requests"] > 0
        assert report["cumulative"]["estimated_usd"] > 0
        assert (http.book / f"final/chapter-{number}.md").read_text() == text
    calls = len(http.requests)
    checkpoint = {p: p.read_bytes() for p in (http.book / "final/.deai").rglob("*") if p.is_file()}
    assert main(["run", str(http.book), "--env", str(http.env)]) == 0
    assert len(http.requests) == calls
    assert all(p.read_bytes() == body for p, body in checkpoint.items())


@pytest.mark.parametrize("quota_vote", [0, 2])
def test_parallel_detector_quota_interrupts_other_waiters(http, quota_vote):
    from tools.runtime_support import QuotaExhausted

    waiting = threading.Event()
    interrupted = []

    def llm(messages, stage, **kwargs):
        if stage.endswith(f"-{quota_vote}"):
            assert waiting.wait(2)
            raise QuotaExhausted("fixture quota")
        waiting.set()
        interrupted.append(transport.STOP.wait(2))
        raise InterruptedError("fixture sibling stopped")

    with pytest.raises(QuotaExhausted):
        Session(llm, {"_semantic_types": ["verb_mismatch"]}).classify(
            "她把信放在门边。", "detector", only=["verb_mismatch"]
        )
    assert interrupted and all(interrupted), "A quota failure must interrupt sibling network waits"


def test_interrupted_backoff_retains_remaining_wait_and_resumes(http, monkeypatch):
    http.replies.append(urllib.error.URLError("fixture offline"))
    normal_pause = transport.pause

    def interrupt(seconds):
        if seconds >= 10:
            transport.STOP.set()
            raise InterruptedError("fixture shutdown")
        normal_pause(seconds)

    monkeypatch.setattr(transport, "pause", interrupt)
    with pytest.raises(InterruptedError):
        transport.MiniMax(http.work, http.env)([], stage="detector")
    assert read_json(http.book / "runtime.json")["kind"] == "interrupted"
    ledger = read_json(http.work / "http/ledger.json")
    assert ledger[0]["retryable"] and ledger[0]["retry_remaining"] > 0
    transport.STOP.clear()
    monkeypatch.setattr(transport, "pause", normal_pause)
    http.replies.append(completion())
    assert transport.MiniMax(http.work, http.env)([], stage="detector") == '{"ok":true}'
    assert len(http.requests) == 2


def test_cli_shutdown_returns_130_without_a_project_error_report(http, monkeypatch):
    from deai import __main__ as cli
    from tools.runtime_support import write_runtime

    save(http.book, "chapter-001.md", "她把信放在门边。")

    def interrupted(*args):
        write_runtime(http.book, "waiting", "fixture-facts", retry=1)
        transport.STOP.set()
        raise InterruptedError("fixture shutdown")

    monkeypatch.setattr(cli, "MiniMax", lambda *args, **kw: SimpleNamespace())
    monkeypatch.setattr("deai.polish", interrupted)
    assert cli.main(["run", str(http.book), "--env", str(http.env)]) == 130
    assert read_json(http.book / "runtime.json")["kind"] == "interrupted"
    assert not (http.book / "final/chapter-001.deai.json").exists()
