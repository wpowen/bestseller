import json
from types import SimpleNamespace
from bookengine.q1_extract import extract_state


def test_extraction_schema_keeps_legacy_solution_fields_with_new_nouns(tmp_path, monkeypatch):
    captured = []

    def ask(client, stage, prompt, sections, schema, **kwargs):
        captured.append(json.loads(schema))
        return {}

    monkeypatch.setattr("bookengine.q1_extract.ask", ask)
    client = SimpleNamespace(out=tmp_path, prefix="")
    bible = dict(canon=dict(ladder={}, hook_types=[], payoff_menu=[], solution_menu=[]))
    extract_state(client, "test-final", "他借影撬开铁门，终于救出同伴。", {}, bible, {"number": 1}, [], final=True)
    solution = captured[0]["solutions"][0]
    assert {"type", "content", "primary", "target_noun", "method_noun", "quote"} <= solution.keys()
