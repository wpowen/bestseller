"""Offline sidecar extraction, literal validation and native client recovery."""

from copy import deepcopy
import io
import json
from pathlib import Path

import pytest
import yaml

from bookengine.storage import load, save


TEXT = "林川以借影复制守卫的开锁动作。借影带来的动作使铁门终于打开，他带着同伴逃出牢房。"
ACTION = "林川以借影复制守卫的开锁动作。"
RESULT = "借影带来的动作使铁门终于打开，他带着同伴逃出牢房。"
BIBLE = {"power": {"name": "借影", "rule": "复制守卫动作"}, "canon": {"solution_menu": ["模仿", "工具"]}}


def raw_plot():
    return {
        "solutions": [
            dict(
                type="模仿",
                content="借影复制动作→铁门打开",
                primary=True,
                target_noun="铁门",
                method_noun="借影",
                quote=TEXT,
            )
        ],
        "core_use": dict(decisive=True, mechanism="借影", action_quote=ACTION, result_quote=RESULT),
    }


def make_book(tmp_path):
    book = tmp_path / "book"
    save(book / "run.json", {"status": "complete", "book": {"chapters": 1}})
    save(book / "chapters/001.json", {"status": "complete", "solutions": []})
    save(book / "story_bible.yaml", yaml.safe_dump(BIBLE, allow_unicode=True))
    save(book / "chapter-001.body.md", TEXT)
    save(book / "final/chapter-001.md", TEXT)
    save(book / "final/chapter-001.deai.json", {"status": "unchanged", "complete": True})
    save(book / "calls.json", [{"status": "old_writer_entry"}])
    save(book / ".pipeline/polish/calls.json", [{"status": "old_polish_entry"}])
    return book


def chapter(book, source="final", text=TEXT):
    path = "final/chapter-001.md" if source == "final" else "chapter-001.body.md"
    return dict(number=1, text=text, path=path)


def observer():
    from bookengine import plot_observations

    return plot_observations


def native_factory(tmp_path, monkeypatch, answer=None):
    calls = []
    env = tmp_path / ".env"
    env.write_text("MINIMAX_API_KEY=offline-placeholder\n", encoding="utf-8")

    def transport(request, timeout):
        payload = json.loads(request.data)
        calls.append(payload)
        assert request.full_url.endswith("/chat/completions")
        assert payload["stream"] is False
        result = dict(
            model="offline-mini",
            choices=[
                dict(message=dict(content=json.dumps(answer or raw_plot(), ensure_ascii=False)), finish_reason="stop")
            ],
            usage=dict(prompt_tokens=23, completion_tokens=17, total_tokens=40),
        )
        return io.BytesIO(json.dumps(result).encode())

    monkeypatch.setattr("urllib.request.urlopen", transport)
    return env, calls


def test_extract_explicit_schema_preserves_raw():
    module = observer()
    captured = {}
    raw = raw_plot()

    def fake_ask(client, stage, instruction, sections, schema, **kwargs):
        captured.update(stage=stage, instruction=instruction, sections=sections, schema=json.loads(schema))
        return raw

    assert module.extract_plot(None, "probe", TEXT, BIBLE, fake_ask) is raw
    solution = captured["schema"]["solutions"][0]
    assert set(solution) == {"type", "content", "primary", "target_noun", "method_noun", "quote"}
    assert set(captured["schema"]["core_use"]) == {"decisive", "mechanism", "action_quote", "result_quote"}
    assert TEXT in [body for _, body in captured["sections"]]
    assert "模仿" in json.dumps(captured, ensure_ascii=False)
    assert all(word in captured["instruction"] for word in ("连续", "不拼接", "因果", "content", "action_quote"))


def test_native_protocol_isolated_audit_and_no_network_resume(tmp_path, monkeypatch):
    module = observer()
    book = make_book(tmp_path)
    before = {p.relative_to(book): p.read_bytes() for p in book.rglob("*") if p.is_file()}
    env, calls = native_factory(tmp_path, monkeypatch)
    result = module.review_book(book, env, source="final")
    assert len(result) == len(calls) == 1
    sidecar = book / "plot-observations/final-001-v1.json"
    saved = load(sidecar)
    assert saved["source_text"] == TEXT
    assert saved["raw"] == raw_plot()
    assert saved["number"] == 1 and saved["source"] == "final"
    assert saved["source_path"] == "final/chapter-001.md"
    assert saved["authority"] == "agent_editorial_not_human"
    assert saved["solutions"][0]["method_noun"] == "借影"
    assert saved["core_use"]["status"] == "verified"
    audit = book / "plot-observer"
    ledger = load(audit / "calls.json")
    assert len(ledger) == 1 and ledger[0]["status"] == "ok"
    assert ledger[0]["stage"] == "plot-final-001-v1"
    assert load(audit / "steps/final-001-v1.input.json") == {
        k: saved[k] for k in ("number", "source", "source_path", "source_text")
    }
    assert ledger[0]["usage"]["total_tokens"] == 40
    assert list((audit / "responses").glob("*.json"))
    assert list((audit / "steps").glob("*.json"))
    assert all((book / p).read_bytes() == content for p, content in before.items())

    def forbidden_factory(*args, **kwargs):
        pytest.fail("unchanged source must resume without client construction")

    assert module.review_book(book, "/missing.env", source="final", client_factory=forbidden_factory) == result
    assert len(calls) == 1
    assert module.load_plot_observation(book, chapter(book), "final")["core_use"]["status"] == "verified"


def test_changed_body_appends_version_preserves_old_audit(tmp_path, monkeypatch):
    module = observer()
    book = make_book(tmp_path)
    env, calls = native_factory(tmp_path, monkeypatch)
    module.review_book(book, env)
    before = {
        p: p.read_bytes()
        for folder in ("plot-observations", "plot-observer/responses", "plot-observer/steps")
        for p in (book / folder).glob("*.json")
    }
    changed = TEXT + "天亮了。"
    save(book / "final/chapter-001.md", changed)
    assert module.load_plot_observation(book, chapter(book, text=changed), "final") is None
    module.review_book(book, env)
    assert len(calls) == 2
    assert all(p.read_bytes() == content for p, content in before.items())
    assert load(book / "plot-observations/final-001-v2.json")["source_text"] == changed
    assert len(load(book / "plot-observer/calls.json")) == 2


@pytest.mark.parametrize(
    "field,value",
    [("number", 2), ("source", "draft"), ("source_path", "chapter-001.body.md"), ("source_text", TEXT + "变了")],
)
def test_loader_rejects_wrong_binding(tmp_path, monkeypatch, field, value):
    module = observer()
    book = make_book(tmp_path)
    env, _ = native_factory(tmp_path, monkeypatch)
    module.review_book(book, env)
    path = book / "plot-observations/final-001-v1.json"
    value_raw = load(path)
    value_raw[field] = value
    save(path, value_raw)
    assert module.load_plot_observation(book, chapter(book), "final") is None


@pytest.mark.parametrize("change", ["quote", "noun", "mechanism", "causal", "missing_content", "type"])
def test_loader_revalidates_raw_never_trusts_saved_verified(tmp_path, monkeypatch, change):
    module = observer()
    book = make_book(tmp_path)
    env, _ = native_factory(tmp_path, monkeypatch)
    module.review_book(book, env)
    path = book / "plot-observations/final-001-v1.json"
    saved = load(path)
    raw = saved["raw"]
    if change == "quote":
        raw["solutions"][0]["quote"] = "他用不存在的阵钉拆阵获胜。"
        raw["core_use"]["action_quote"] = "他使用了不存在的能力，终于获胜。"
    elif change == "noun":
        raw["solutions"][0]["method_noun"] = "阵钉"
    elif change == "mechanism":
        raw["core_use"]["mechanism"] = "借影、复制守卫动作"
    elif change == "causal":
        raw["core_use"]["result_quote"] = ACTION
    elif change == "missing_content":
        raw["solutions"][0].pop("content")
    else:
        raw["solutions"][0]["type"] = "非法类型"
    save(path, saved)
    result = module.load_plot_observation(book, chapter(book), "final")
    if change in ("quote", "noun", "missing_content", "type"):
        assert result["solutions"] == []
    if change in ("quote", "mechanism", "causal"):
        assert result["core_use"]["status"] == "unknown"


def test_native_response_recovery_without_resend(tmp_path, monkeypatch):
    module = observer()
    book = make_book(tmp_path)
    env, calls = native_factory(tmp_path, monkeypatch)
    original_save = module.save

    def crash(path, value):
        if Path(path).parent.name == "plot-observations":
            raise InterruptedError("before sidecar checkpoint")
        return original_save(path, value)

    monkeypatch.setattr(module, "save", crash)
    with pytest.raises(InterruptedError):
        module.review_book(book, env)
    assert len(calls) == 1
    for path in (book / "plot-observer/steps").glob("*.json"):
        if not path.name.endswith(".input.json"):
            path.unlink()  # Response saved before the native stage checkpoint; input binding survives.
    monkeypatch.setattr(module, "save", original_save)
    module.review_book(book, env)
    assert len(calls) == 1
    ledger = load(book / "plot-observer/calls.json")
    assert len(ledger) == 1 and ledger[0]["reused_response"] is True
    assert ledger[0]["usage"]["total_tokens"] == 40


def test_changed_body_after_crash_cannot_reuse_old_step(tmp_path, monkeypatch):
    module = observer()
    book = make_book(tmp_path)
    env, calls = native_factory(tmp_path, monkeypatch)
    original_save = module.save

    def crash(path, value):
        if Path(path).parent.name == "plot-observations":
            raise InterruptedError("before sidecar")
        original_save(path, value)

    monkeypatch.setattr(module, "save", crash)
    with pytest.raises(InterruptedError):
        module.review_book(book, env)
    save(book / "final/chapter-001.md", TEXT + "次日。")
    monkeypatch.setattr(module, "save", original_save)
    module.review_book(book, env)
    assert len(calls) == 2


@pytest.mark.parametrize("missing", ["writer", "final", "final_report", "chapter"])
def test_preflight_rejects_incomplete_book_before_client(tmp_path, missing):
    module = observer()
    book = make_book(tmp_path)
    if missing == "writer":
        save(book / "run.json", {"status": "running", "book": {"chapters": 1}})
    else:
        paths = {
            "final": "final/chapter-001.md",
            "final_report": "final/chapter-001.deai.json",
            "chapter": "chapters/001.json",
        }
        (book / paths[missing]).unlink()

    def forbidden(*args, **kwargs):
        pytest.fail("preflight must precede client construction")

    with pytest.raises(ValueError, match="complete|齐全|完成"):
        module.review_book(book, "/missing.env", client_factory=forbidden)
    assert not (book / "plot-observer").exists()


def test_draft_and_final_separate_and_injection(tmp_path):
    module = observer()
    book = make_book(tmp_path)
    seen = []

    def factory(out, env, prefix):
        seen.append(Path(out))
        return object()

    def fake_ask(*args, **kwargs):
        return deepcopy(raw_plot())

    module.review_book(book, "/missing.env", source="draft", client_factory=factory, ask=fake_ask)
    assert seen == [book / "plot-observer"]
    assert module.load_plot_observation(book, chapter(book, "draft"), "draft")["source"] == "draft"
    assert module.load_plot_observation(book, chapter(book), "final") is None
    assert module.load_plot_observation(book, chapter(book, "draft"), "final") is None


def test_preflight_uses_shared_web_total_fallback(tmp_path):
    module = observer()
    book = make_book(tmp_path)
    save(book / "run.json", {"status": "complete"})
    save(book / "web.json", {"chapters": 1})
    result = module.review_book(
        book, "/missing.env", client_factory=lambda *args: None, ask=lambda *args, **kwargs: raw_plot()
    )
    assert len(result) == 1


def test_loader_rejects_merged_mechanism_even_when_literal(tmp_path):
    module = observer()
    book = make_book(tmp_path)
    action = "林川以借影、复制动作打开了铁门。"
    text = action + RESULT
    bible = deepcopy(BIBLE)
    bible["power"]["rule"] = "借影、复制动作"
    save(book / "story_bible.yaml", yaml.safe_dump(bible, allow_unicode=True))
    save(book / "final/chapter-001.md", text)
    raw = raw_plot()
    raw["core_use"].update(mechanism="借影、复制动作", action_quote=action)
    save(
        book / "plot-observations/final-001-v1.json",
        dict(
            number=1,
            source="final",
            source_path="final/chapter-001.md",
            source_text=text,
            raw=raw,
            solutions=[],
            core_use={"status": "verified"},
        ),
    )
    result = module.load_plot_observation(book, chapter(book, text=text), "final")
    assert result["core_use"]["status"] == "unknown"


def test_sources_and_chapter_override_cannot_ground_fabricated_quote(tmp_path):
    module = observer()
    book = make_book(tmp_path)
    raw = raw_plot()
    raw["solutions"][0].update(chapter=0, quote="他用阵钉使铁门打开。", method_noun="阵钉")
    raw["_sources"] = {0: "他用阵钉使铁门打开。"}
    result = module.review_book(
        book, "/missing.env", client_factory=lambda *args: None, ask=lambda *args, **kwargs: raw
    )
    assert result[0]["solutions"] == []


def test_corrupted_sidecar_is_ignored_without_overwriting_it(tmp_path):
    module = observer()
    book = make_book(tmp_path)
    broken = book / "plot-observations/final-001-v1.json"
    save(broken, "{broken")
    assert module.load_plot_observation(book, chapter(book), "final") is None
    result = module.review_book(
        book, "/missing.env", client_factory=lambda *args: None, ask=lambda *args, **kwargs: raw_plot()
    )
    assert result[0]["core_use"]["status"] == "verified"
    assert broken.read_text() == "{broken"
    assert (book / "plot-observations/final-001-v2.json").exists()
