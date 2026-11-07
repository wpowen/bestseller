"""INBOX 43: cost accounting and bounded de-AI model work."""

import json

from deai.costs import analyze_book
from deai.flow import Session, polish
from deai.rules import detect


class RecordingClient:
    def __init__(self):
        self.calls = []

    def __call__(self, messages, *, stage, **kwargs):
        data = json.loads(messages[-1]["content"].split("\n")[-1])
        self.calls.append(dict(stage=stage, data=data, usage=None))
        if "examples" in data:
            return dict(checked_pids=[part["pid"] for part in data["paragraphs"]], issues=[])
        if "targets" in data:
            return dict(candidates=[dict(sid=item["sid"], choices=[]) for item in data["targets"]])
        if "items" in data:
            return dict(checks=[dict(id=item["id"], ok=True, reason="fixture") for item in data["items"]])
        return dict(checked_pids=[part["pid"] for part in data["paragraphs"]], facts=[], chapter={})


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))


def test_cost_breakdown_accounts_by_phase_and_adoption(tmp_path):
    _write(
        tmp_path / "final/.deai/001/http/ledger.json",
        [
            dict(
                stage="recall22-initial-verb_mismatch-0",
                status="ok",
                usage=dict(prompt_tokens=100, completion_tokens=10),
            ),
            dict(
                stage="recall22-recheck-verb_mismatch-0",
                status="ok",
                usage=dict(prompt_tokens=50, completion_tokens=5),
            ),
            dict(stage="recall22-facts", status="error", usage=None),
        ],
    )
    _write(
        tmp_path / "final/chapter-001.deai.json",
        dict(
            status="polished",
            hits=[dict(type="verb_mismatch")],
            edits=[dict(types=["verb_mismatch"], pid="P2")],
        ),
    )

    result = analyze_book(tmp_path)

    assert result["totals"] == dict(calls=3, prompt_tokens=150, completion_tokens=15, unknown_usage=1)
    assert result["phases"]["diagnosis"]["tokens"] == 110
    assert result["phases"]["recheck"]["tokens"] == 55
    assert result["rule_categories"]["verb_mismatch"] == dict(hits=1, adopted_edits=1, adopted_chapters=1)


def test_irrelevant_semantic_categories_make_no_model_calls():
    client = RecordingClient()
    hits, unknown = Session(client, {}).classify("她进屋，把门关上。", "recall22-initial")

    assert hits == [] and unknown == []
    assert client.calls == []


def test_semantic_prefilter_sends_only_candidate_paragraphs():
    client = RecordingClient()
    text = "她进屋，把门关上。\n\n午后的闷热把她的嗓子折成了两截。\n\n他坐下喝水。"

    Session(client, {}).classify(text, "recall22-initial")

    semantic = [call for call in client.calls if call["stage"].startswith("recall22-initial-")]
    assert semantic
    assert {part["pid"] for call in semantic for part in call["data"]["paragraphs"]} == {"P2"}


def test_recheck_only_reads_changed_paragraphs():
    client = RecordingClient()
    text = "她进屋，把门关上。\n\n他沉声道：“现在走。”\n\n院里的人还在等回话。"

    result = polish(text, {}, client)

    assert result.status == "polished"
    rechecks = [call for call in client.calls if call["stage"].startswith("recall22-recheck-")]
    assert rechecks
    assert {part["pid"] for call in rechecks for part in call["data"]["paragraphs"]} == {"P2"}


def test_bad_verb_object_collocations_are_deterministic_candidates():
    examples = ("那道影子发出无声的压迫。", "这一次，没有人替他们保证归处。")

    for text in examples:
        assert any(hit["type"] == "verb_mismatch" for hit in detect(text))
    repaired = ("那道影子散发出无声的压迫。", "这一次，没有人替他们保证有归处。")
    for text in repaired:
        assert not any(hit["type"] == "verb_mismatch" for hit in detect(text))


def test_blind_negative_rule_remains_reported_but_is_not_rewritten():
    client = RecordingClient()
    text = "风不再只是声音，雪粒落下的轨迹、冻雪覆盖下的裂隙、岩层之间细微的温差，一层层变得清晰。"

    result = polish(text, {}, client)

    assert any(hit["type"] == "long_de_chain" for hit in result.hits)
    assert result.status == "unchanged"
    assert not any("targets" in call["data"] for call in client.calls)


def test_known_bad_collocations_use_fact_preserving_deterministic_edits():
    client = RecordingClient()
    text = "那道影子发出无声的压迫，门边的人都没有说话。\n\n这一次，没有人替他们保证归处。"

    result = polish(text, {}, client)

    assert "散发出无声的压迫" in result.text
    assert "保证有归处" in result.text
    assert result.status == "polished"
    assert all(edit["source"] == "deterministic" for edit in result.edits)
    assert not any("items" in call["data"] for call in client.calls)
