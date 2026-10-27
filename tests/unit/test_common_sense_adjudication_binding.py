from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import exports, reviews
from bestseller.services.common_sense_gate import _find_lay_character_rule_knowledge_leak


def test_explicit_reading_a_visible_clause_is_not_unearned_knowledge():
    text = "纸上写着“连续两日”，连续两日是赔偿条件。陶然指着第三行，念出声：“若连续两日未拍到孩子上车视频，每次赔偿半月接送费五十元。”"
    assert not _find_lay_character_rule_knowledge_leak(text, chapter_number=2)


def test_unexplained_knowledge_still_blocks_after_someone_else_reads():
    text = "条款写着“录名”，录名之后名字归局里。陶然指着第三行，念出声：“录名者不得出门。”\n\n张建军：“我已经知道录名之后如何夺走别人的名字。”"
    assert _find_lay_character_rule_knowledge_leak(text, chapter_number=2)


def test_concrete_evidence_reaches_adjudication_prompt():
    from bestseller.services.common_sense_gate import CommonSenseFinding
    from bestseller.services.gate_adjudicator import build_adjudication_prompts

    finding = CommonSenseFinding(
        code="lay_character_rule_knowledge_leak",
        severity="medium",
        message="缺少学习来源",
        evidence={"window": "某人说了一个术语", "terms": ["录名"]},
    )
    converted = SimpleNamespace(
        category="common_sense",
        severity="medium",
        message=reviews._common_sense_finding_message(finding),
    )
    _, prompt = build_adjudication_prompts(
        [converted], text="正文", genre="悬疑", sub_genre=None, language="zh"
    )
    assert "某人说了一个术语" in prompt and "录名" in prompt


@pytest.mark.parametrize("change", ["none", "body", "draft", "legacy"])
def test_export_only_honors_current_body_adjudication(change):
    draft = SimpleNamespace(id=uuid4(), content_md="当前正文")
    receipt = reviews._common_sense_adjudication_receipt(
        draft, ["lay_character_rule_knowledge_leak"]
    )
    chapter = SimpleNamespace(
        metadata_json={
            "common_sense_dismissed_codes": ["lay_character_rule_knowledge_leak"],
            "common_sense_adjudication": receipt,
        }
    )
    if change == "body":
        draft.content_md += "已修改"
    if change == "draft":
        draft.id = uuid4()
    if change == "legacy":
        chapter.metadata_json.pop("common_sense_adjudication")
    assert bool(exports._current_common_sense_dismissed_codes(chapter, draft)) is (change == "none")


def test_new_review_clears_old_dismissal_even_for_same_draft():
    import inspect
    import textwrap

    draft = SimpleNamespace(id=uuid4(), content_md="当前正文")
    chapter = SimpleNamespace(
        metadata_json={
            "common_sense_dismissed_codes": ["lay_character_rule_knowledge_leak"],
            "common_sense_adjudication": reviews._common_sense_adjudication_receipt(
                draft, ["lay_character_rule_knowledge_leak"]
            ),
        }
    )
    source = inspect.getsource(reviews.review_chapter_draft)
    start = source.index("    # An earlier draft/review")
    end = source.index("    # --- LLM adjudication", start)
    namespace = dict(vars(reviews), chapter=chapter, draft=draft)
    exec(textwrap.dedent(source[start:end]), namespace)
    assert chapter.metadata_json["common_sense_dismissed_codes"] == []
    assert exports._current_common_sense_dismissed_codes(chapter, draft) == set()


def test_reading_exception_does_not_grant_unexplained_understanding():
    text = (
        "条款写着“录名”，录名之后名字归局里。张建军盯着第三行说：“我早知道录名能夺走别人的名字。”"
    )
    findings = _find_lay_character_rule_knowledge_leak(text, chapter_number=2)
    assert findings and findings[0].evidence["terms"] == ["录名"]


def test_visible_clause_reading_then_literal_wording_reference():
    text = (
        "纸上写着“提前通知”，提前通知是这份合同的条件。"
        "李青指着第二条，念出声：“提前通知之后才可以改期。”\n\n"
        "她把手指移到第三行，“‘提前通知’是关键词。”\n\n"
        "她用笔尖点住“提前通知”四个字，“这几个字已经写在合同里。”"
    )
    assert not _find_lay_character_rule_knowledge_leak(text, chapter_number=2)
