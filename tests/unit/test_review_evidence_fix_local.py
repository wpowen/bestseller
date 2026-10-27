from types import SimpleNamespace

from bestseller.services.critic_evidence_gate import validate_critic_commentary
from bestseller.services.reviews import chapter_review_body_fingerprint
from bestseller.services import reviews as review_services
from bestseller.services import pipelines as pipeline_services
from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores


def test_fabricated_quote_is_rejected_by_body_evidence_gate():
    result = validate_critic_commentary(
        'EVIDENCE: “这是一段正文里不存在的虚构引文。” COMMENTARY: 章节很好。',
        chapter_text='陆衡举起镐头，砸向岩壁。',
    )
    assert result.passed is False


def test_exact_quote_is_accepted_by_body_evidence_gate():
    result = validate_critic_commentary(
        'EVIDENCE: “陆衡举起镐头，砸向岩壁。” COMMENTARY: 这一处动作推进了冲突。',
        chapter_text='陆衡举起镐头，砸向岩壁。',
    )
    assert result.passed is True


def test_all_evidence_quotes_must_exist_and_only_evidence_region_counts():
    body = "陆衡举起镐头，砸向岩壁。"
    mixed = validate_critic_commentary(
        'EVIDENCE: “陆衡举起镐头，砸向岩壁。” “正文中不存在的引文。”\n'
        'COMMENTARY: 术语“陆衡举起镐头，砸向岩壁。”不能替代证据。',
        chapter_text=body,
    )
    term_only = validate_critic_commentary(
        'EVIDENCE: 没有正文摘录。\n'
        'COMMENTARY: 术语“陆衡举起镐头，砸向岩壁。”不能替代证据。',
        chapter_text=body,
    )
    assert mixed.passed is False
    assert term_only.passed is False


def test_empty_reviewed_body_is_rejected_even_with_a_quoted_excerpt():
    result = validate_critic_commentary(
        'EVIDENCE: “陆衡举起镐头，砸向岩壁。” COMMENTARY: 证据对应动作轴。',
        chapter_text="",
    )
    assert result.passed is False
    assert any(f.code == "CRITIC_BODY_EVIDENCE_UNAVAILABLE" for f in result.findings)


def test_main_review_fingerprint_binds_english_and_numeric_changes():
    body = "Chapter 1: code 42\r\nThe gate opens."
    same_newlines = "Chapter 1: code 42\nThe gate opens."
    changed_english = "Chapter 1: code 43\nThe gate opens."
    changed_number = "Chapter 1: code 42\nThe gate closes."

    assert chapter_review_body_fingerprint(body) == chapter_review_body_fingerprint(same_newlines)
    assert chapter_review_body_fingerprint(body) != chapter_review_body_fingerprint(changed_english)
    assert chapter_review_body_fingerprint(body) != chapter_review_body_fingerprint(changed_number)


def _pass_review() -> ChapterReviewResult:
    return ChapterReviewResult(
        verdict="pass",
        severity_max="major",
        scores=ChapterReviewScores(
            overall=0.9,
            goal=0.9,
            coverage=0.9,
            coherence=0.9,
            continuity=0.9,
            main_plot_progression=0.9,
            subplot_progression=0.9,
            style=0.9,
            hook=0.9,
            ending_hook_effectiveness=0.9,
            volume_mission_alignment=0.9,
            pacing_rhythm=0.9,
            character_voice_distinction=0.9,
            thematic_resonance=0.9,
            contract_alignment=0.9,
        ),
    )


def test_unverified_or_fallback_commentary_cannot_override_deterministic_verdict():
    response = (
        "VERDICT: rewrite\nMETHODOLOGY: hook\nREWRITE_DIRECTION: 加固结尾\n"
        "EVIDENCE: “这是正文里的具体动作和后果，足以作为可核对的章节摘录。”\n"
        "COMMENTARY: 证据对应追读轴。"
    )
    invalid = review_services._apply_verified_chapter_review_override(
        _pass_review(), response, evidence_verified=False
    )
    valid = review_services._apply_verified_chapter_review_override(
        _pass_review(), response, evidence_verified=True
    )
    assert invalid.verdict == "pass"
    assert valid.verdict == "rewrite"
    assert review_services._is_real_chapter_review_completion(
        SimpleNamespace(provider="fallback", fallback_used=True, finish_reason="fallback")
    ) is False


def test_only_complete_non_synthetic_provider_completion_is_usable():
    assert review_services._is_real_chapter_review_completion(
        SimpleNamespace(provider="anthropic", finish_reason="stop", content="正文评审")
    ) is True
    assert review_services._is_real_chapter_review_completion(
        SimpleNamespace(provider="mock", finish_reason="stop", content="正文评审")
    ) is False
    assert review_services._is_real_chapter_review_completion(
        SimpleNamespace(provider="", finish_reason="stop", content="正文评审")
    ) is False
    assert review_services._is_real_chapter_review_completion(
        SimpleNamespace(provider="unknown", finish_reason="stop", content="正文评审")
    ) is False
    assert review_services._is_real_chapter_review_completion(
        SimpleNamespace(provider="anthropic", finish_reason="length", content="正文评审")
    ) is False
    assert review_services._is_real_chapter_review_completion(
        SimpleNamespace(provider="anthropic", finish_reason="stop", content="")
    ) is False


def test_promotion_consumer_rejects_missing_stale_or_wrong_draft_review_body():
    draft = SimpleNamespace(id="draft-a", content_md="正文版本 A，包含英文 code 42。")
    matching = SimpleNamespace(
        structured_output={
            "draft_id": "draft-a",
            "reviewed_body_fingerprint": chapter_review_body_fingerprint(draft.content_md),
        }
    )
    body_changed = SimpleNamespace(
        structured_output={
            "draft_id": "draft-a",
            "reviewed_body_fingerprint": chapter_review_body_fingerprint("正文版本 A，包含英文 code 41。"),
        }
    )
    other_draft = SimpleNamespace(
        structured_output={
            "draft_id": "draft-b",
            "reviewed_body_fingerprint": chapter_review_body_fingerprint(draft.content_md),
        }
    )
    missing_hash = SimpleNamespace(structured_output={"draft_id": "draft-a"})

    assert pipeline_services._chapter_review_body_binding_code(matching, draft) is None
    assert pipeline_services._chapter_review_body_binding_code(body_changed, draft) == "REVIEW_BODY_STALE"
    assert pipeline_services._chapter_review_body_binding_code(other_draft, draft) == "REVIEW_BODY_STALE"
    assert pipeline_services._chapter_review_body_binding_code(missing_hash, draft) == "REVIEW_BODY_UNVERIFIED"
    assert pipeline_services._chapter_review_body_binding_code(None, draft) == "REVIEW_BODY_UNVERIFIED"
