import yaml

from tools.book_checkup import check_book


def test_checkup_passes_bible_to_identity_scan(tmp_path):
    bible = {
        "characters": [
            {
                "name": "林冬岚",
                "role": "protagonist",
                "identity": "北城唯一仍然守灯的少年",
                "address": "砚哥",
                "address_origin": "幼年描过一块砚台",
            },
        ]
    }
    (tmp_path / "story_bible.yaml").write_text(yaml.safe_dump(bible, allow_unicode=True))
    (tmp_path / "chapter-001.body.md").write_text("北城唯一仍然守灯的少年站在桥头。有人喊了一声砚哥。")
    report = check_book(tmp_path)
    assert report["prose_similarity"]["card_explanations"] == 1
