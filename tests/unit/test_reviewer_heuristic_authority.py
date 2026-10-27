from types import SimpleNamespace as N

import pytest

from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult
from bestseller.services import reviews
from bestseller.settings import load_settings


pytestmark = pytest.mark.unit

GENRES = ("现实职场", "科幻", "悬疑", "古典奇幻")


@pytest.mark.parametrize("genre", GENRES)
@pytest.mark.parametrize(
    "prose",
    (
        "围观者举起手机拍下清点过程，检修员当面核对封条。",
        "同伴把电话递给检查员，接线员说出封条编号后挂断。",
        "检查员按下录音键，随后请在场双方各自陈述。",
    ),
)
def test_opening_plan_silence_does_not_prohibit_devices(genre, prose):
    chapter = N(chapter_number=1, opening_situation="检修员在库房当面清点。", genre=genre)
    scene = N(title="当面清点", hook_requirement="", purpose={}, entry_state={})

    assert reviews._chapter_opening_contract_findings(chapter, [scene], prose) == []


@pytest.mark.parametrize("genre", GENRES)
@pytest.mark.parametrize(
    "planned,prose",
    (
        ("检修员站在电梯口", "轿厢停稳，检查员扶住门边，将封袋递给同伴。"),
        ("检查员在镜面前确认封条", "玻璃映出封袋上的编号，她看清字迹后签收。"),
    ),
)
def test_paraphrased_space_does_not_require_plan_phrase(genre, planned, prose):
    chapter = N(chapter_number=2, opening_situation=planned, genre=genre)
    scene = N(title=planned, hook_requirement="", purpose={}, entry_state={})

    assert reviews._chapter_opening_contract_findings(chapter, [scene], prose) == []


def _review(genre, language, *, duplicate=False):
    body = (
        "她把封袋放在桌上，核对编号后才让同伴签收。\n\n"
        "对方要求立即拆封，她摇头，把留存单推过去：“先签收，再查验。”\n\n"
        "同伴签下名字。她带走回执，留下封袋，门外的脚步却停了下来。"
    )
    contract = N(
        information_release="The archive contains the missing star chart.",
        character_delta="Astra forfeits her seniority and accepts the demotion.",
        protagonist_choice="Astra chooses to surrender her navigation license.",
    )
    return reviews.evaluate_chapter_draft(
        chapter=N(chapter_number=2, chapter_goal="核验交接", target_word_count=200),
        scenes=[N(scene_number=1, title="交接")],
        draft=N(content_md=body, word_count=200, assembled_from_scene_draft_ids=["scene"]),
        settings=load_settings(env={}),
        chapter_contract=contract,
        genre=genre,
        language=language,
        duplication_score=0.2 if duplicate else 1.0,
        duplication_findings=(
            [{"severity": "critical", "message": "This body repeats an earlier passage."}]
            if duplicate else []
        ),
    )


@pytest.mark.parametrize("genre", GENRES)
@pytest.mark.parametrize("language", ("zh-CN", "en"))
def test_other_rewrite_does_not_promote_contract_audit_to_repair(genre, language):
    result = _review(genre, language, duplicate=True)

    assert result.verdict == "rewrite"
    assert any(f.category == "duplication" for f in result.findings)
    assert any(f.category == "contract_alignment" for f in result.findings)
    missing = result.evidence_summary["contract_missing_labels"]
    assert {"character_delta", "protagonist_choice"} <= set(missing)
    assert result.evidence_summary["contract_alignment_breakdown"]["character_delta"] == 0
    assert result.evidence_summary["contract_alignment_breakdown"]["protagonist_choice"] == 0
    assert result.scores.contract_alignment < 0.5
    assert result.rewrite_instructions
    assert all(label not in result.rewrite_instructions for label in missing)


@pytest.mark.parametrize("genre", GENRES)
def test_supported_commercial_block_still_requires_rewrite(genre):
    original = _review(genre, "zh-CN")
    judge = LLMQualityJudgeResult.model_validate({
        "pass": False,
        "overall_score": 0.6,
        "blocking_issues": [{
            "code": "HANDOFF_CAUSALITY_CONFLICT",
            "severity": "high",
            "evidence": "The recipient signs for an object that the sender already took away.",
            "required_fix": "Make custody at the signing moment consistent with the handoff.",
        }],
        "rewrite_plan": {"instructions": "Clarify custody before the receipt is signed."},
    })

    blocked = reviews._merge_llm_quality_judge_into_chapter_review(
        original,
        category="llm_commercial_quality",
        message_prefix="商业质量评测",
        judge_result=judge,
    )

    assert blocked.verdict == "rewrite"
    assert blocked.scores == original.scores
    assert blocked.evidence_summary == original.evidence_summary
    assert any("HANDOFF_CAUSALITY_CONFLICT" in f.message for f in blocked.findings)
    assert "custody" in blocked.rewrite_instructions
    assert not reviews._can_accept_llm_pass_over_rule_rewrite(
        blocked, judge.model_dump(mode="json", by_alias=True)
    )


@pytest.mark.parametrize("genre", GENRES)
def test_explicit_prohibition_is_preserved_in_repair_guard(genre):
    chapter = N(
        chapter_number=2,
        genre=genre,
        opening_situation="核对封条。",
        metadata_json={"object_signal_contract": {"forbidden_signals": ["封条自行发光"]}},
        foreshadowing_actions={},
    )
    scene = N(forbidden_actions=["不得用电话代替本人签收。"])

    instructions = reviews._sanitize_chapter_review_rewrite_instructions(
        "让封条自行发光，证明签收人身份。\n保留本人签收动作。",
        chapter=chapter,
        scenes=[scene],
    )

    assert "让封条自行发光" not in instructions
    assert "不得用电话代替本人签收" in instructions
    assert "保留本人签收动作" in instructions
