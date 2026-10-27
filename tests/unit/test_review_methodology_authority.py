from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores
from bestseller.services import reviews
from bestseller.services.methodology_runtime import merge_methodology_reports_into_chapter_review
from bestseller.services.narrative_obligation_migration import bind_explicit_obligation

pytestmark = pytest.mark.unit


def explicit(mode):
    return {
        "creation_intent_contract": {
            "field_sources": {"hook_spec": "explicit"},
            "hook_spec": {"require_methodology_overlay": True, "methodology_contract_mode": mode},
        }
    }


def passing_review():
    return ChapterReviewResult(
        verdict="pass",
        severity_max="info",
        scores=ChapterReviewScores(**dict.fromkeys(ChapterReviewScores.model_fields, 0.9)),
    )


def inputs(metadata, genre="quiet travel"):
    project = SimpleNamespace(id=uuid4(), metadata_json=metadata, genre=genre, language="en")
    chapter = SimpleNamespace(
        id=uuid4(),
        chapter_number=2,
        title="The garden",
        chapter_goal="Visit the garden",
        main_conflict=None,
        hook_description=None,
        hype_type=None,
        metadata_json={},
    )
    draft = SimpleNamespace(
        id=uuid4(),
        version_no=1,
        content_md="Neri waters the garden. The leaves glisten.",
    )
    return project, chapter, draft


@pytest.fixture(autouse=True)
def strict_global_config(monkeypatch):
    monkeypatch.setattr(
        reviews,
        "_methodology_framework_config",
        lambda: SimpleNamespace(
            opening_three_function_enabled=True,
            opening_three_function_default="strict",
            opening_three_function_block_until_chapter=3,
            chekhov_emphasis_enabled=True,
            chekhov_emphasis_default="strict",
        ),
    )


@pytest.mark.parametrize("genre", ["quiet travel", "space exploration", "historical family"])
@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"methodology_contract_mode": "strict", "source": "user"},
        {"creation_intent_contract": {"field_sources": {"hook_spec": "explicit"}, "hook_spec": {}}},
        {
            "creation_intent_contract": {
                "field_sources": {"hook_spec": "user"},
                "hook_spec": {
                    "require_methodology_overlay": True,
                    "methodology_contract_mode": "strict",
                },
            }
        },
        {
            "creation_intent_contract": {
                "field_sources": {"story_enhancers": "explicit"},
                "story_enhancers": {"effect_skills": ["comedy"]},
            }
        },
        explicit("off"),
    ],
)
async def test_unselected_opening_recipe_does_not_become_review_obligation(metadata, genre):
    before = deepcopy(metadata)
    project, chapter, draft = inputs(metadata, genre)
    reports = await reviews._compute_chapter_methodology_reports(
        session=object(),
        project=project,
        chapter=chapter,
        draft=draft,
        chapter_context=None,
    )
    merged = merge_methodology_reports_into_chapter_review(passing_review(), reports)
    assert merged.verdict == "pass" and merged.severity_max == "info"
    assert not any("OPENING_" in f.message for f in merged.findings)
    assert metadata == before


@pytest.mark.parametrize("mode", ["strict", "warn"])
async def test_explicit_opening_policy_is_respected_through_review_merge(mode):
    project, chapter, draft = inputs(explicit(mode))
    reports = await reviews._compute_chapter_methodology_reports(
        session=object(),
        project=project,
        chapter=chapter,
        draft=draft,
        chapter_context=None,
    )
    merged = merge_methodology_reports_into_chapter_review(passing_review(), reports)
    assert any("OPENING_CH2_COST_PROOF_MISSING" in f.message for f in merged.findings)
    assert any("OPENING_CH2_CHOICE_ACTION_MISSING" in f.message for f in merged.findings)
    if mode == "strict":
        assert merged.verdict == "rewrite" and merged.severity_max == "critical"
    else:
        assert merged.verdict == "pass" and merged.severity_max == "info"
        assert merged.rewrite_instructions is None
        opening = next(r for r in reports if r.agent == "opening-three-function-gate")
        assert opening.metrics["raw_checker_report"]["issues"][0]["severity"] == "high"


async def test_source_bound_due_commitment_remains_checked_with_overlay_off():
    item = {
        "label": "sealed letter",
        "expected_function": "deliver the letter",
        "expected_use_by_chapter": 2,
        "status": "active",
        "prominence": "high",
    }
    source = {
        "id": "approved-outline",
        "project_id": "isolated-test",
        "status": "approved",
        "artifact_type": "chapter_outline_batch",
        "content": {"items": [item]},
    }
    bound = {**item, "narrative_obligation": bind_explicit_obligation(source, ["items", 0])}
    project, chapter, draft = inputs({})
    chapter.metadata_json = {"methodology_contract": {"emphasized_items": [bound]}}
    before = deepcopy(chapter.metadata_json)
    reports = await reviews._compute_chapter_methodology_reports(
        session=object(),
        project=project,
        chapter=chapter,
        draft=draft,
        chapter_context=None,
    )
    merged = merge_methodology_reports_into_chapter_review(passing_review(), reports)
    assert any("CHEKHOV_USE_OVERDUE" in f.message for f in merged.findings)
    assert merged.verdict == "rewrite"
    assert chapter.metadata_json == before


async def test_unproven_due_label_is_not_a_commitment():
    project, chapter, draft = inputs({})
    chapter.metadata_json = {
        "methodology_contract": {
            "emphasized_items": [
                {
                    "label": "old calendar",
                    "source": "user",
                    "expected_use_by_chapter": 1,
                    "status": "active",
                    "prominence": "high",
                },
            ]
        }
    }
    reports = await reviews._compute_chapter_methodology_reports(
        session=object(),
        project=project,
        chapter=chapter,
        draft=draft,
        chapter_context=None,
    )
    assert not any(r.issues for r in reports)


async def test_splice_gate_still_runs_when_methodology_is_off():
    project, chapter, draft = inputs({})
    sentence = "The traveler carefully folded the blue map and placed it beside the brass compass."
    draft.content_md = "\n\n".join([sentence] * 6)
    reports = await reviews._compute_chapter_methodology_reports(
        session=object(),
        project=project,
        chapter=chapter,
        draft=draft,
        chapter_context=None,
    )
    assert any("SPLICE" in issue.id for report in reports for issue in report.issues)


class CapturedReviewError(Exception):
    def __init__(self, result):
        self.result = result


async def review_prefix(monkeypatch, metadata, *, declared=(), raw_due=(), payoff_audit=None):
    """Run the actual call/output-gate block; stop before unrelated LLM/persistence."""
    project, chapter, draft = inputs(metadata)
    project.sub_genre = None
    draft.word_count = 2200
    contract = SimpleNamespace(
        methodology_declared_payoffs=list(declared), due_payoff_codes=list(raw_due)
    )
    context = SimpleNamespace(chapter_contract=contract)
    monkeypatch.setenv("BESTSELLER_METHODOLOGY_V2", "1")
    monkeypatch.setattr(
        reviews,
        "_load_chapter_context",
        AsyncMock(return_value=(project, chapter, None, [], draft)),
    )
    monkeypatch.setattr(reviews, "build_chapter_writer_context", AsyncMock(return_value=context))
    monkeypatch.setattr(
        reviews, "_compute_chapter_duplication_signal", AsyncMock(return_value=(1.0, []))
    )
    monkeypatch.setattr(reviews, "evaluate_chapter_draft", lambda **kw: passing_review())
    monkeypatch.setattr(reviews, "_compute_chapter_methodology_reports", AsyncMock(return_value=()))
    monkeypatch.setattr(
        reviews, "compute_hook_ledger_audit_for_review", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        reviews, "compute_payoff_ledger_audit_for_review", AsyncMock(return_value=payoff_audit)
    )
    for name in (
        "_compute_chapter_antagonist_scope_signal",
        "_compute_premature_death_signal",
        "_compute_chapter_seam_signal",
        "_compute_reader_logic_signal",
        "_compute_stitched_draft_signal",
    ):
        monkeypatch.setattr(reviews, name, AsyncMock(return_value=([], {})))
    monkeypatch.setattr(reviews, "_compute_countdown_regression_signal", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        reviews, "_compute_name_canon_signal", AsyncMock(return_value=([object()], {}))
    )

    def capture(result, *args, **kwargs):
        raise CapturedReviewError(result)

    monkeypatch.setattr(reviews, "_merge_name_canon_into_review", capture)
    with pytest.raises(CapturedReviewError) as stopped:
        await reviews.review_chapter_draft(object(), SimpleNamespace(), "isolated-test", 2)
    assert (
        reviews.compute_payoff_ledger_audit_for_review.call_args.kwargs["chapter_contract"] is None
    )
    assert contract.methodology_declared_payoffs == list(declared)
    assert contract.due_payoff_codes == list(raw_due)
    return stopped.value.result


@pytest.mark.parametrize("mode", ["off", "warn", "strict"])
async def test_real_review_prefix_accepts_empty_payoff_declaration(monkeypatch, mode):
    result = await review_prefix(monkeypatch, explicit(mode), raw_due=["old-ai-cache"])
    assert result.verdict == "pass" and result.severity_max == "info"
    assert not any("PAYOFFS_DUE_EMPTY" in f.message for f in result.findings)


@pytest.mark.parametrize("mode", ["off", "warn", "strict"])
@pytest.mark.parametrize("required", [[], ["deliver-letter"]])
async def test_only_literal_current_chapter_payoffs_can_require_declarations(
    monkeypatch, mode, required
):
    metadata = explicit(mode)
    metadata["creation_intent_contract"]["hook_spec"]["stage_contracts"] = {
        "review": {"chapters": {"2": {"payoffs_due": required}}}
    }
    before = deepcopy(metadata)
    result = await review_prefix(monkeypatch, metadata)
    blocking = bool(required) and mode != "warn"
    assert result.verdict == ("rewrite" if blocking else "pass")
    assert result.severity_max == ("major" if blocking else "info")
    assert metadata == before


@pytest.mark.parametrize("fault", ["wrong_chapter", "book_palette", "source_user"])
async def test_payoff_authority_does_not_spread_from_another_scope(monkeypatch, fault):
    metadata = explicit("strict")
    hook = metadata["creation_intent_contract"]["hook_spec"]
    if fault == "book_palette":
        hook["payoffs_due"] = ["unscheduled-book-goal"]
    else:
        hook["stage_contracts"] = {
            "review": {
                "chapter_number": 3 if fault == "wrong_chapter" else 2,
                "payoffs_due": ["deliver-letter"],
            }
        }
    if fault == "source_user":
        metadata["creation_intent_contract"]["field_sources"]["hook_spec"] = "user"
    result = await review_prefix(monkeypatch, metadata)
    assert result.verdict == "pass" and result.severity_max == "info"


async def test_real_due_ledger_is_not_disabled_by_empty_overlay(monkeypatch):
    from bestseller.services.payoff_ledger import run_payoff_ledger_audit

    payoff = SimpleNamespace(
        payoff_code="letter-due",
        label="letter",
        description="deliver letter",
        source_clue_id=None,
        target_chapter_number=2,
        actual_chapter_number=None,
        status="planned",
        metadata_json={},
    )
    audit = run_payoff_ledger_audit([payoff], current_chapter=2)
    result = await review_prefix(monkeypatch, {}, payoff_audit=audit)
    assert result.verdict == "rewrite"
    assert any("PAYOFF_DUE_UNRESOLVED" in f.message for f in result.findings)
