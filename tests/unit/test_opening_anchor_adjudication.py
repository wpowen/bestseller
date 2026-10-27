from types import SimpleNamespace as N
from uuid import uuid4

import pytest

from bestseller.services import gate_adjudicator as ga, reviews


def anchor_finding():
    """Retained historical finding, independent of the retired lexical detector."""
    anchors = ["书包侧面反光镜面拍到手印"]
    prose = "手机亮了。书包侧面那片反光镜面把手印拍成一团模糊。"
    return reviews.ChapterReviewFinding(
        category="opening_contract",
        severity="medium",
        message=(
            "OPENING_SCENE_ANCHOR_MISSING: 正文前500字没有落到第一场开场锚点："
            + "、".join(anchors)
            + "\n预期开场锚点：" + repr(anchors)
            + "\n实际开场前500字：" + prose
        ),
    ), prose


def test_only_free_text_anchor_enters_contextual_adjudication():
    finding, prose = anchor_finding()
    assert ga.is_adjudicable(finding)
    assert not ga.is_adjudicable(
        N(
            category="opening_contract",
            severity="high",
            message="OPENING_SCENE_DRIFT: unplanned scene",
        )
    )
    _, prompt = ga.build_adjudication_prompts(
        [finding], text=prose, genre="现实", sub_genre=None, language="zh"
    )
    assert "预期开场锚点" in prompt and prose in finding.message


@pytest.mark.parametrize(
    "response,allowed",
    [
        ("1: DISMISS - 同一物件动作的自然改写", True),
        ("1: CONFIRM - 正文实际换到餐馆，完全没有镜面", False),
        ("无法确定", False),
        (None, False),
    ],
)
async def test_anchor_adjudication_fails_closed(monkeypatch, response, allowed):
    async def complete(*args):
        if response is None:
            raise RuntimeError("unavailable")
        return N(content=response, model_name="test", llm_run_id=uuid4())

    monkeypatch.setattr(ga, "complete_text", complete)
    finding, prose = anchor_finding()
    result = await ga.adjudicate_findings(
        None,
        N(pipeline=N(gate_llm_adjudication_enabled=True)),
        N(id=None, slug="test", genre="现实", sub_genre=None, language="zh"),
        chapter_number=3,
        text=prose,
        findings=[finding],
    )
    assert bool(result.dismissed) is allowed
    assert bool(result.confirmed) is not allowed
    receipt = reviews._gate_adjudication_receipt(N(id=uuid4(), content_md=prose), result)
    assert receipt["response"] == (response if response is not None else "1: CONFIRM")
    assert receipt["reviewed_body_fingerprint"] == reviews.chapter_review_body_fingerprint(prose)


def test_receipt_is_bound_to_exact_draft_and_body():
    draft = N(id=uuid4(), content_md="当前开场")
    result = ga.AdjudicationResult()
    first = reviews._gate_adjudication_receipt(draft, result)
    draft.content_md += "修改"
    changed_body = reviews._gate_adjudication_receipt(draft, result)
    assert first["reviewed_body_fingerprint"] != changed_body["reviewed_body_fingerprint"]
    draft.id = uuid4()
    assert reviews._gate_adjudication_receipt(draft, result)["draft_id"] != first["draft_id"]


def test_new_review_discards_prior_opening_adjudication():
    import inspect
    import textwrap

    draft = N(id=uuid4(), content_md="当前开场")
    chapter = N(
        metadata_json={
            "gate_adjudication": {"dismissed_codes": ["OPENING_SCENE_ANCHOR_MISSING"]},
            "requires_human_review": True,
        }
    )
    source = inspect.getsource(reviews.review_chapter_draft)
    start = source.index("    # An earlier draft/review")
    end = source.index("    # --- LLM adjudication", start)
    exec(textwrap.dedent(source[start:end]), dict(vars(reviews), chapter=chapter, draft=draft))
    assert chapter.metadata_json["gate_adjudication"] is None
    assert chapter.metadata_json["requires_human_review"] is True


@pytest.mark.parametrize("dismissed", [True, False])
def test_opening_adjudication_recomputes_hard_evidence(dismissed):
    from bestseller.settings import load_settings

    anchor, _ = anchor_finding()
    findings = [N(category="continuity", severity="high", message="advisory echo")]
    if not dismissed:
        findings.append(anchor)
    result = N(
        scores=N(
            overall=0.60,
            goal=0.99,
            coverage=0.90,
            coherence=0.78,
            style=0.80,
            duplication_score=1.0,
        )
    )
    result.verdict = reviews._chapter_verdict_after_adjudication(
        result,
        findings,
        settings=load_settings(),
        genre="现实百态",
        sub_genre="现实百态",
        chapter_number=3,
    )
    result.findings = findings
    result.evidence_summary = {"hard_gates_passed": False, "blocking_codes": ["stale"]}
    evidence = reviews._promotion_evidence_from_review(result)
    assert evidence["hard_gates_passed"] is (True if dismissed else None)
    assert evidence["review_gate_passed"] is dismissed
    assert evidence["independent_hard_safety_status"] == "unknown"
    assert result.verdict == ("pass" if dismissed else "rewrite")
    assert evidence["blocking_codes"] == []
