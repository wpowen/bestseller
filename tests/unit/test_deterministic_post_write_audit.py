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


def test_signature_image_match_normalizes_chinese_quote_variants(tmp_path):
    class QuoteScene:
        scene_number = 1
        metadata_json: ClassVar[dict] = {
            "methodology_contract": {
                "signature_image": "开场以「熟悉地点出现不该存在的第二层空间」切入"
            }
        }

    report = audit_chapter_prose(
        chapter_text="他按住铜钱，冷光一闪。开场以“熟悉地点出现不该存在的第二层空间”切入。最后他问：是谁？",
        chapter_number=1,
        project_dir=_project_dir(tmp_path),
        scenes=[QuoteScene()],
    )

    assert not any(item.code == "SIGNATURE_IMAGE_MISSING" for item in report.findings)


def test_signature_image_global_presence_survives_segment_misalignment(tmp_path):
    class FirstScene:
        scene_number = 1
        metadata_json: ClassVar[dict] = {
            "methodology_contract": {
                "signature_image": "铜钱发亮",
                "cut_point": "第一场断点没有出现在正文里",
            }
        }

    class SecondScene:
        scene_number = 2
        metadata_json: ClassVar[dict] = {
            "methodology_contract": {
                "signature_image": "门后传来响声",
                "cut_point": "第二场断点没有出现在正文里",
            }
        }

    report = audit_chapter_prose(
        chapter_text=(
            "他推开门，先听见风声压住楼道。"
            "门后传来响声。"
            "直到第二段调查结束，铜钱发亮，照出墙上的旧痕。"
            "最后他问：是谁？"
        ),
        chapter_number=1,
        project_dir=_project_dir(tmp_path),
        scenes=[FirstScene(), SecondScene()],
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


def test_ending_approaching_threat_without_question_mark_passes(tmp_path):
    report = audit_chapter_prose(
        chapter_text="他把簿子压回袖中。甬道那头脚步声更近了。不是一个人。",
        chapter_number=1,
        project_dir=_project_dir(tmp_path),
    )

    assert not any(item.code == "ENDING_HOOK_MISSING" for item in report.findings)


def test_hook_two_sentences_before_a_closing_image_passes(tmp_path):
    """Chapter-first prose lands the hook, then adds one quiet closing image.

    A live chapter (2026-07-22) ended on a red sub-signature about to appear and
    a bronze token routing to the mine's true owner — strong forward suspense —
    then one image sentence. The 120-char window saw only the image and flagged
    it hookless; it was repaired twice and still flagged. The window is now 220.
    """

    report = audit_chapter_prose(
        chapter_text=(
            "他抬起头。外门名册“待甄别”三个字下方，今夜会多出一个从未见过的红色副签；"
            "而那面已经录下落点的铜令，正沿着他不知道的某条规则链，向这座矿脉真正的"
            "主人递过去。灰雾盖住了半边光线，山门的轮廓在远处若隐若现。"
        ),
        chapter_number=1,
        project_dir=_project_dir(tmp_path),
    )

    assert not any(item.code == "ENDING_HOOK_MISSING" for item in report.findings)


def test_genuinely_flat_ending_still_flags(tmp_path):
    """Widening the window must not turn the gate off. A chapter that truly
    peters out with no unresolved thread must still be caught."""

    report = audit_chapter_prose(
        chapter_text=(
            "他回到住处，洗净手上的血，躺下。窗外月色很好，风也停了。这一天就这样过去，"
            "明天又是寻常的一天。他闭上眼，没有再想任何事情，很快睡熟。"
        ),
        chapter_number=1,
        project_dir=_project_dir(tmp_path),
    )

    assert any(item.code == "ENDING_HOOK_MISSING" for item in report.findings)


def test_signature_image_accepts_natural_paraphrase(tmp_path):
    class XianxiaScene:
        scene_number = 1
        metadata_json: ClassVar[dict] = {
            "methodology_contract": {"signature_image": "炭灰里跪稳的跛足"}
        }

    report = audit_chapter_prose(
        chapter_text="她的膝盖落进炭灰，跛足的节奏稳住，手才没有发抖。最后门外有人停下。",
        chapter_number=1,
        project_dir=_project_dir(tmp_path),
        scenes=[XianxiaScene()],
    )

    assert not any(item.code == "SIGNATURE_IMAGE_MISSING" for item in report.findings)


def test_paragraph_duplicate_paraphrase_high_similarity_caught(tmp_path):
    paragraph = "他按住铜钱，冷光一闪，门后传来响声。"
    report = audit_chapter_prose(
        chapter_text=f"{paragraph}\n\n{paragraph}\n\n最后他问：是谁？",
        chapter_number=1,
        project_dir=_project_dir(tmp_path),
    )

    assert any(item.code == "PARAGRAPH_DUPLICATE_PARAPHRASE" for item in report.findings)


def test_scene_card_long_dialogue_copy_is_blocked(tmp_path):
    class CopyScene:
        scene_number = 1
        metadata_json: ClassVar[dict] = {}
        key_dialogue_beats: ClassVar[list[str]] = [
            "顾箬把铜签推回去说，这份账今天谁碰，谁就得把命留下。"
        ]
        sensory_anchors: ClassVar[dict] = {}

    report = audit_chapter_prose(
        chapter_text=(
            "许照川推开木门，冷风卷起账页。"
            "顾箬把铜签推回去说，这份账今天谁碰，谁就得把命留下。"
            "门外忽然有人敲了三下。"
        ),
        chapter_number=1,
        project_dir=_project_dir(tmp_path),
        scenes=[CopyScene()],
    )

    assert any(item.code == "SCENE_CARD_PROSE_COPIED" for item in report.findings)
    assert not report.passed


def test_scene_card_facts_and_short_fragments_are_not_copy_findings(tmp_path):
    class FactScene:
        scene_number = 1
        metadata_json: ClassVar[dict] = {}
        key_dialogue_beats: ClassVar[list[str]] = ["打开账本"]
        sensory_anchors: ClassVar[dict] = {"object": "铜签"}

    report = audit_chapter_prose(
        chapter_text="许照川打开账本，把铜签压在纸角。门外忽然有人问：谁在里面？",
        chapter_number=1,
        project_dir=_project_dir(tmp_path),
        scenes=[FactScene()],
    )

    assert not any(item.code == "SCENE_CARD_PROSE_COPIED" for item in report.findings)
