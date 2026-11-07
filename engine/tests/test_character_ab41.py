"""The character A/B harness reuses saved design and planning without regenerating them."""

import json

import yaml

from tools.character_ab import blind_preference, load_inputs


def test_ab_loader_uses_saved_bible_volume_and_first_five_outlines(tmp_path):
    bible = {"title": "旧书", "characters": [{"name": "林宁"}], "canon": {}}
    threads = {"threads": [{"id": "main"}]}
    volume = {"number": 1, "start": 1, "end": 10}
    (tmp_path / "steps").mkdir()
    (tmp_path / "writing_outlines").mkdir()
    (tmp_path / "story_bible.yaml").write_text(yaml.safe_dump(bible, allow_unicode=True))
    (tmp_path / "threads.yaml").write_text(yaml.safe_dump(threads, allow_unicode=True))
    (tmp_path / "steps/plan-q1-volume-1-growth2.json").write_text(json.dumps(volume))
    for number in range(1, 7):
        (tmp_path / f"writing_outlines/{number:03d}.json").write_text(json.dumps({"number": number}))
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    loaded = load_inputs(tmp_path)

    assert loaded == (bible, threads, volume, [{"number": number} for number in range(1, 6)])
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


def test_blind_preference_anonymizes_sides_and_maps_result_back(tmp_path):
    left = tmp_path / "old-context"
    right = tmp_path / "new-context"
    left.mkdir()
    right.mkdir()
    for number in range(1, 6):
        (left / f"chapter-{number:03d}.body.md").write_text(f"旧稿第{number}章")
        (right / f"chapter-{number:03d}.body.md").write_text(f"新稿第{number}章")

    class Judge:
        family = "Fixture"
        model = "fixture-judge"
        same_family = True

        def stage(self, name, prompt, **kwargs):
            assert name == "character-ab-preference"
            payload = json.loads(prompt)
            assert set(payload["versions"]) == {"甲", "乙"}
            assert "old-context" not in prompt
            assert "new-context" not in prompt
            return {"winner": "甲", "remembered": {"甲": ["林宁"], "乙": ["周青"]}, "reason": "甲更鲜明"}

    output = tmp_path / "preference.json"
    result = blind_preference(left, right, Judge(), output, swap=True)

    assert result["winner"] == "B"
    assert result["anonymous_order"] == {"甲": "B", "乙": "A"}
    assert result["remembered"] == {"A": ["周青"], "B": ["林宁"]}
    assert result["same_family_as_writer"] is True
    assert json.loads(output.read_text()) == result
