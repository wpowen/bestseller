"""Genre-neutrality of the commercial quality judges (F1–F7).

These lock in the 2026-06 fix that removed the single-genre (detective/exorcism)
contamination from the chapter / outline / planning-readiness judges and bound the
acceptance floor to the writer model tier. See
docs/prompt-methodology-fusion-audit-2026-06.md and judge_genre_context.py.
"""

# Short uppercase module aliases (J/O) and fullwidth punctuation in Chinese asserts.
# ruff: noqa: N812, RUF002

from __future__ import annotations

import pytest

from bestseller.services import chapter_llm_quality_judge as J
from bestseller.services import outline_llm_judge as O
from bestseller.services.judge_genre_context import (
    GENERIC_CORPUS_KEY,
    available_reference_corpus_keys,
    derive_specialist_rule_terms,
    resolve_judge_genre_context,
    resolve_reference_corpus_key,
)
from bestseller.services.judge_rubrics import get_judge_rubric

_DETECTIVE_LEAK_TERMS = ("青囊", "罗盘", "铜钱", "认账", "镜债", "账线", "阴阳眼", "困魂镜")


# ---------------------------------------------------------------------------
# F1 — judge_genre_context resolver
# ---------------------------------------------------------------------------


def test_detective_resolves_to_suspense_corpus_and_commission():
    ctx = resolve_judge_genre_context(genre="悬疑探案", sub_genre="都市怪谈")
    assert ctx.category_key == "suspense-mystery"
    assert ctx.corpus_key == "suspense-mystery"
    assert ctx.uses_commission_structure is True


@pytest.mark.parametrize(
    "genre,sub_genre,expected_cat",
    [
        ("都市言情", "破镜重圆", "relationship-driven"),
        ("玄幻", "升级流", "action-progression"),
        ("历史", "权谋争霸", "strategy-worldbuilding"),
    ],
)
def test_non_detective_never_uses_detective_corpus(genre, sub_genre, expected_cat):
    ctx = resolve_judge_genre_context(genre=genre, sub_genre=sub_genre)
    assert ctx.category_key == expected_cat
    # The whole bug: a non-detective book must NEVER be scored against the
    # suspense-mystery corpus. It gets its own corpus if present, else generic.
    assert ctx.corpus_key != "suspense-mystery"
    assert ctx.corpus_key in (expected_cat, GENERIC_CORPUS_KEY)
    assert ctx.uses_commission_structure is False


def test_romance_story_logic_is_relationship_not_commission():
    ctx = resolve_judge_genre_context(genre="都市言情")
    joined = "\n".join(ctx.story_logic_checks_zh)
    assert "委托人" not in joined
    assert "人设立体" in joined


def test_own_terms_derived_from_bible_not_hardcoded():
    bible = {"worldview": {"power_system": {"terms": ["真元", "丹田", "噬灵诀"]}}}
    ctx = resolve_judge_genre_context(genre="玄幻", story_bible=bible)
    assert "真元" in ctx.specialist_terms
    for leak in _DETECTIVE_LEAK_TERMS:
        assert leak not in ctx.specialist_terms


def test_derive_specialist_rule_terms_empty_when_no_bible():
    assert derive_specialist_rule_terms(None) == ()
    assert derive_specialist_rule_terms({}) == ()


# ---------------------------------------------------------------------------
# F4 — corpus resolution + loader fallback
# ---------------------------------------------------------------------------


def test_generic_corpus_exists():
    assert GENERIC_CORPUS_KEY in available_reference_corpus_keys()


def test_unknown_genre_falls_back_to_generic_not_detective():
    assert resolve_reference_corpus_key("nonexistent-genre") == GENERIC_CORPUS_KEY


def test_corpus_loader_returns_generic_for_missing_genre():
    corpus = J._load_reference_corpus("relationship-driven")  # no file on disk
    assert corpus is not None
    assert corpus.get("genre") == "generic"


# ---------------------------------------------------------------------------
# F2 — chapter judge system prompt is genre-neutral
# ---------------------------------------------------------------------------


def test_chapter_judge_prompt_has_no_detective_leak_for_other_genre():
    ctx = resolve_judge_genre_context(
        genre="玄幻", sub_genre="升级流",
        story_bible={"power_system": {"terms": ["真元", "噬灵诀"]}},
    )
    sp = J._render_chapter_judge_system_prompt(
        rubric=get_judge_rubric("chapter_commercial"),
        reference_block="", checklist_block="", calibration_block="",
        genre_context=ctx,
    )
    for leak in _DETECTIVE_LEAK_TERMS:
        assert leak not in sp, f"detective term leaked into chapter judge: {leak}"
    assert "真元" in sp  # this book's own term IS present


# ---------------------------------------------------------------------------
# F6 — outline + planning judges neutralize the commission structure
# ---------------------------------------------------------------------------


def test_outline_judge_neutral_for_romance_but_commission_for_detective():
    romance = resolve_judge_genre_context(genre="都市言情")
    detective = resolve_judge_genre_context(genre="悬疑探案")
    rub = get_judge_rubric("outline_commercial")
    s_romance = O._render_outline_commercial_system_prompt(
        rubric=rub, methodology_reference="", genre_context=romance
    )
    s_detective = O._render_outline_commercial_system_prompt(
        rubric=rub, methodology_reference="", genre_context=detective
    )
    assert "委托人" not in s_romance
    assert "人设立体" in s_romance
    assert "委托" in s_detective  # detective keeps its commission framing


def test_planning_judge_neutral_for_romance():
    romance = resolve_judge_genre_context(genre="都市言情")
    rub = get_judge_rubric("commercial_planning")
    sp = O._render_planning_readiness_system_prompt(
        rubric=rub, methodology_reference="", genre_context=romance
    )
    assert "委托人选择动机" not in sp
    assert "关系张力" in sp


def test_render_functions_backward_compatible_without_context():
    # Legacy callers that pass no genre_context keep the original commission block.
    rub = get_judge_rubric("outline_commercial")
    legacy = O._render_outline_commercial_system_prompt(
        rubric=rub, methodology_reference=""
    )
    assert "委托人选择动机" in legacy


# ---------------------------------------------------------------------------
# F7 — acceptance floor bound to writer model tier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,is_premium",
    [
        ("minimax-m3", False),
        ("deepseek-chat", False),
        ("claude-opus-4-5", True),
        ("claude-sonnet-4-5", True),
        ("gpt-4o", True),
    ],
)
def test_is_premium_writer_model(model, is_premium):
    assert J.is_premium_writer_model(model) is is_premium


def test_thresholds_bound_to_writer_model():
    minimax_overall, minimax_dims = J.chapter_commercial_thresholds(1, None, "minimax-m3")
    claude_overall, claude_dims = J.chapter_commercial_thresholds(1, None, "claude-opus-4-5")
    assert minimax_overall == pytest.approx(0.85)
    assert claude_overall == pytest.approx(0.92)
    assert minimax_dims["opening_pull"] == pytest.approx(0.82)
    assert claude_dims["opening_pull"] == pytest.approx(0.90)


def test_thresholds_no_model_uses_corpus_band():
    overall, _ = J.chapter_commercial_thresholds(1, None, None)
    assert overall == pytest.approx(0.85)  # corpus/default band, not premium


# ---------------------------------------------------------------------------
# R2 — commercial judge model is independently selectable
# ---------------------------------------------------------------------------


def test_llm_request_accepts_model_catalog_key():
    from bestseller.services.llm import LLMCompletionRequest

    req = LLMCompletionRequest(
        logical_role="critic", system_prompt="x", user_prompt="y", fallback_response="z",
        model_catalog_key="claude-tier-judge",
    )
    assert req.model_catalog_key == "claude-tier-judge"
    # default is None → unchanged behaviour for every existing caller
    base = LLMCompletionRequest(
        logical_role="critic", system_prompt="x", user_prompt="y", fallback_response="z",
    )
    assert base.model_catalog_key is None


def test_resolve_commercial_judge_model_key_from_env(monkeypatch):
    class _S:
        class llm:  # no explicit field → falls through to env
            pass

    monkeypatch.delenv("BESTSELLER__LLM__COMMERCIAL_JUDGE_MODEL_KEY", raising=False)
    assert J.resolve_commercial_judge_model_key(_S()) is None
    monkeypatch.setenv("BESTSELLER__LLM__COMMERCIAL_JUDGE_MODEL_KEY", "claude-judge")
    assert J.resolve_commercial_judge_model_key(_S()) == "claude-judge"
