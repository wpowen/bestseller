"""Instruction 41 character-baseline evaluation stays source-bound and auditable."""

import json

from bookengine.character_diagnostics import character_reference, dialogue_samples, score_guesses, verify_fulfillment
from tools.character_eval import evaluate_book, judge_profile
from tools.book_checkup import check_book


def _bible():
    return {
        "characters": [
            {
                "name": "林宁",
                "role": "protagonist",
                "flaw": "逞强时拒绝求援",
                "bottom_line": "不拿无辜者换命",
                "voice": "被关心时先反问",
                "catchphrase": "先救人。",
            },
            {"name": "周青", "role": "support", "voice": "生气时省掉称呼"},
            {"name": "许沅", "role": "romance", "voice": "痛处被问时岔开"},
            {"name": "顾衡", "role": "antagonist", "voice": "威胁时先谈规矩"},
        ],
        "antagonists": [{"name": "顾衡", "tier": 1, "stage": 1, "exit": 5}],
    }


def _chapters():
    names = ["林宁", "周青", "许沅", "顾衡"]
    chapters = []
    for chapter in range(1, 6):
        lines = [f"{name}说：“第{chapter}次轮到我说这句话。”" for name in names]
        if chapter == 1:
            lines.append("林宁说：“先救人。”")
        chapters.append({"number": chapter, "text": "\n".join(lines)})
    return chapters


def test_dialogue_blind_samples_cover_four_people_and_remove_all_labels():
    samples = dialogue_samples(_bible(), _chapters(), minimum=20)

    assert len(samples) == 20
    assert {sample["speaker"] for sample in samples} == {"林宁", "周青", "许沅", "顾衡"}
    labels = [card["name"] for card in _bible()["characters"]]
    assert all(not any(label in sample["masked"] for label in labels) for sample in samples)


def test_blind_score_reports_per_character_accuracy_and_confusion():
    samples = dialogue_samples(_bible(), _chapters(), minimum=20)
    guesses = [
        {"id": sample["id"], "speaker": "周青" if sample["speaker"] == "林宁" else sample["speaker"]}
        for sample in samples
    ]
    result = score_guesses(samples, guesses)

    assert result["correct"] == 15
    assert result["accuracy"] == 0.75
    assert result["per_character"]["林宁"]["accuracy"] == 0.0
    assert result["confusion"] == [{"actual": "林宁", "guessed": "周青", "count": 5}]


def test_fulfillment_quotes_must_exist_in_the_claimed_chapter():
    raw = {
        "findings": [
            {"character": "林宁", "field": "catchphrase", "chapter": 1, "quote": "先救人。"},
            {"character": "林宁", "field": "flaw", "chapter": 2, "quote": "正文里不存在的引文"},
        ]
    }
    result = verify_fulfillment(raw, _bible(), _chapters())

    assert result["林宁"]["catchphrase"]["status"] == "Evidence"
    assert result["林宁"]["flaw"]["status"] == "Unknown"
    assert result["林宁"]["bottom_line"]["status"] == "Unknown"
    assert result["林宁"]["voice"]["status"] == "Unknown"


def test_reference_counts_antagonist_chapters_mentions_and_dialogue():
    result = character_reference(_bible(), _chapters())
    antagonist = result["antagonist_presence"][0]

    assert antagonist == {
        "name": "顾衡",
        "chapters": [1, 2, 3, 4, 5],
        "mention_count": 5,
        "dialogue_count": 5,
        "status": "Evidence",
    }
    assert result["blind_sample_pool"]["sample_count"] == 20
    assert result["blind_sample_pool"]["speaker_count"] == 4


def test_book_checkup_includes_reference_and_verified_saved_model_result(tmp_path):
    import yaml

    (tmp_path / "story_bible.yaml").write_text(yaml.safe_dump(_bible(), allow_unicode=True))
    for chapter in _chapters():
        (tmp_path / f"chapter-{chapter['number']:03d}.body.md").write_text(chapter["text"])
    saved = {"schema_version": 1, "judge_family": "DeepSeek", "blind_test": {"accuracy": 0.75}}
    (tmp_path / "character_evaluation.json").write_text(json.dumps(saved, ensure_ascii=False))

    report = check_book(tmp_path)

    assert report["character_diagnostics"]["reference"]["blind_sample_pool"]["sample_count"] == 20
    assert report["character_diagnostics"]["model_evaluation"] == saved


def test_judge_profile_prefers_configured_family_different_from_writer(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "DEEPSEEK_API_KEY=judge-placeholder\n"
        "MINIMAX_API_KEY=writer-placeholder\n"
        "BESTSELLER__LLM__WRITER__API_BASE=https://api.minimaxi.com/v1\n"
        "BESTSELLER__LLM__WRITER__API_KEY_ENV=MINIMAX_API_KEY\n"
    )

    profile = judge_profile(env)

    assert profile == {
        "family": "DeepSeek",
        "model": "deepseek-flash",
        "api_base": "https://api.deepseek.com",
        "key_env": "DEEPSEEK_API_KEY",
        "same_family": False,
    }
    assert judge_profile(env, prefer_different=False)["same_family"] is True


def test_model_evaluation_is_verified_then_saved_for_checkup(tmp_path):
    import yaml

    (tmp_path / "story_bible.yaml").write_text(yaml.safe_dump(_bible(), allow_unicode=True))
    for chapter in _chapters():
        (tmp_path / f"chapter-{chapter['number']:03d}.body.md").write_text(chapter["text"])

    class Judge:
        model = "fixture-judge"
        family = "Fixture"
        same_family = False

        def stage(self, name, prompt, **kwargs):
            if name == "character-blind":
                return {
                    "guesses": [
                        {"id": sample["id"], "speaker": sample["speaker"]}
                        for sample in dialogue_samples(_bible(), _chapters())
                    ]
                }
            assert name == "character-fulfillment"
            return {
                "findings": [
                    {"character": "林宁", "field": "catchphrase", "chapter": 1, "quote": "先救人。"},
                    {"character": "林宁", "field": "flaw", "chapter": 1, "quote": "伪造引文"},
                ]
            }

    result = evaluate_book(tmp_path, Judge())

    assert result["blind_test"]["accuracy"] == 1.0
    assert result["fulfillment"]["林宁"]["catchphrase"]["status"] == "Evidence"
    assert result["fulfillment"]["林宁"]["flaw"]["status"] == "Unknown"
    assert json.loads((tmp_path / "character_evaluation.json").read_text()) == result
    assert check_book(tmp_path)["character_diagnostics"]["model_evaluation"] == result
