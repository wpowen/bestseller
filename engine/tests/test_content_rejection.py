"""Provider content moderation (MiniMax 1026/1027) must not stop a whole book."""

import io
import json
from unittest.mock import patch
import urllib.error

import pytest

from bookengine.client import Client, ContentRejected
from bookengine.content_guard import guarded


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
    return Reply(dict(choices=[dict(message=dict(content=text), finish_reason="stop")], usage={}))


def rejected(side="output"):
    code = 1027 if side == "output" else 1026
    body = json.dumps(
        {"type": "error", "error": {"type": "unprocessable_entity_error", "message": f"{side} new_sensitive ({code})"}}
    ).encode()
    return urllib.error.HTTPError("https://fake.invalid", 422, "unprocessable", {}, io.BytesIO(body))


def test_output_rejection_is_retried_with_a_restraint_note_and_then_succeeds(tmp_path):
    c = client(tmp_path)
    prompts = []

    def send(request, **_):
        prompts.append(json.loads(request.data)["messages"][-1]["content"])
        if len(prompts) == 1:
            raise rejected("output")
        return good()

    with patch("urllib.request.urlopen", side_effect=send):
        assert c.stage("ch5-final-state", "正文") == "正文已完成。"
    assert len(prompts) == 2
    assert "内容审核" in prompts[1] and "内容审核" not in prompts[0]
    assert c.calls[0]["status"] == "content_rejected"


def test_repeated_rejection_raises_content_rejected_not_a_generic_project_error(tmp_path):
    c = client(tmp_path)
    with patch("urllib.request.urlopen", side_effect=[rejected("input") for _ in range(10)]) as http:
        with pytest.raises(ContentRejected):
            c.stage("ch5-momentum-hook-recheck", "正文")
    assert http.call_count == 3
    assert json.loads((tmp_path / "runtime.json").read_text())["kind"] == "error"


def test_business_code_rejection_on_http_200_is_recognised(tmp_path):
    c = client(tmp_path)
    reply = Reply(dict(base_resp=dict(status_code=1027, status_msg="output new_sensitive")))
    with patch("urllib.request.urlopen", side_effect=[reply, good()]):
        assert c.stage("body", "正文") == "正文已完成。"


def test_guarded_optional_repair_keeps_the_text_when_the_provider_refuses():
    def repair(client, prefix, text, raw, failed, *rest):
        raise ContentRejected("ch5-momentum-hook-recheck: content rejected")

    text, raw, failed, note = guarded(repair)(None, "ch5", "原文", {"a": 1}, [], "more")
    assert (text, raw, failed) == ("原文", {"a": 1}, [])
    assert note["status"] == "skipped_content_rejected"


def test_guarded_passes_through_normal_results_and_other_errors():
    def ok(client, prefix, text, raw, failed):
        return text + "改", raw, failed, {"status": "ok"}

    assert guarded(ok)(None, "p", "文", {}, [])[0] == "文改"

    def broken(client, prefix, text, raw, failed):
        raise RuntimeError("real bug")

    with pytest.raises(RuntimeError):
        guarded(broken)(None, "p", "文", {}, [])


def test_input_rejected_draft_is_rewritten_with_restraint_under_new_stage_names():
    from bookengine.content_guard import draft_until_readable

    drafts, finishes = [], []

    def write(suffix):
        drafts.append(suffix)
        return "稿" + suffix

    def finish(suffix, text):
        finishes.append((suffix, text))
        if suffix == "":
            raise ContentRejected("ch10-finish: input new_sensitive (1026)")
        return ({"ok": True}, [], None)

    text, result, variant = draft_until_readable(write, finish)
    assert drafts == ["", "-restrained1"]
    assert (text, variant) == ("稿-restrained1", "-restrained1")
    assert result[0] == {"ok": True}


def test_every_variant_rejected_still_stops_the_book():
    from bookengine.content_guard import draft_until_readable

    def finish(suffix, text):
        raise ContentRejected("refused")

    with pytest.raises(ContentRejected):
        draft_until_readable(lambda suffix: "稿", finish)
