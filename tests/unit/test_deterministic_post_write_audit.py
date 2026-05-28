import json
from typing import ClassVar

# ruff: noqa: RUF001
from bestseller.services.deterministic_post_write_audit import audit_chapter_prose


class FakeScene:
    scene_number = 1
    hook_requirement = ""
    metadata_json: ClassVar[dict] = {"methodology_contract": {"signature_image": "铜钱发亮"}}


def _project_dir(tmp_path):
    story_bible = tmp_path / "story-bible"
    story_bible.mkdir()
    (story_bible / "canon-guardrails.json").write_text(
        json.dumps({"forbidden_terms": [{"term": "旧设定", "suggestion": "新设定"}]}),
        encoding="utf-8",
    )
    return tmp_path


def test_forbidden_term_in_text_returns_critical_finding(tmp_path):
    report = audit_chapter_prose(
        chapter_text="他按住铜钱，冷光一闪，旧设定出现了。最后他问：是谁？",
        chapter_number=1,
        project_dir=_project_dir(tmp_path),
    )

    assert any(item.code == "FORBIDDEN_TERM_HIT" for item in report.findings)
    assert not report.passed


def test_signature_image_present_passes(tmp_path):
    report = audit_chapter_prose(
        chapter_text="他按住铜钱，冷光一闪。铜钱发亮，门后传来响声。最后他问：是谁？",
        chapter_number=1,
        project_dir=_project_dir(tmp_path),
        scenes=[FakeScene()],
    )

    assert not any(item.code == "SIGNATURE_IMAGE_MISSING" for item in report.findings)


def test_opening_first_100_chars_lacking_action_verb_fails(tmp_path):
    report = audit_chapter_prose(
        chapter_text="夜色非常安静，空气沉闷得像一块布。最后他问：是谁？",
        chapter_number=1,
        project_dir=_project_dir(tmp_path),
    )

    assert any(item.code == "OPENING_PRESSURE_THIN" for item in report.findings)


def test_ending_120_chars_with_question_mark_passes(tmp_path):
    report = audit_chapter_prose(
        chapter_text="他按住铜钱，冷光一闪，门后传来响声。最后他问：是谁？",
        chapter_number=1,
        project_dir=_project_dir(tmp_path),
    )

    assert not any(item.code == "ENDING_HOOK_MISSING" for item in report.findings)


def test_paragraph_duplicate_paraphrase_high_similarity_caught(tmp_path):
    paragraph = "他按住铜钱，冷光一闪，门后传来响声。"
    report = audit_chapter_prose(
        chapter_text=f"{paragraph}\n\n{paragraph}\n\n最后他问：是谁？",
        chapter_number=1,
        project_dir=_project_dir(tmp_path),
    )

    assert any(item.code == "PARAGRAPH_DUPLICATE_PARAPHRASE" for item in report.findings)
