"""Genre-neutrality of the commercial quality judges (F1–F7).

These lock in the 2026-06 fix that removed the single-genre (detective/exorcism)
contamination from the chapter / outline / planning-readiness judges and bound the
acceptance floor to the writer model tier. See
docs/prompt-methodology-fusion-audit-2026-06.md and judge_genre_context.py.
"""

# Short uppercase module aliases (J/O) and fullwidth punctuation in Chinese asserts.
# ruff: noqa: N812, RUF002

from __future__ import annotations

import json

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
from bestseller.services.llm import LLMCompletionResult
from bestseller.settings import load_settings

_DETECTIVE_LEAK_TERMS = ("青囊", "罗盘", "铜钱", "认账", "镜债", "账线", "阴阳眼", "困魂镜")


@pytest.mark.parametrize("renderer,rubric_name", [
    (O._render_outline_commercial_system_prompt, "outline_commercial"),
    (O._render_planning_readiness_system_prompt, "commercial_planning"),
])
def test_absent_genre_context_does_not_restore_legacy_commission_template(renderer, rubric_name):
    prompt = renderer(rubric=get_judge_rubric(rubric_name), methodology_reference="", genre_context=None)
    assert "为什么找主角而不是 110" not in prompt
    assert "委托人会主动找主角，而非 110/120/物业" not in prompt
    assert "家学传承 / 师承 / 前案口碑 / 熟人引介 / 行业身份 之一" not in prompt


def test_outline_review_does_not_treat_blueprint_form_or_author_knowledge_as_prose_leak():
    prompt = O._render_outline_commercial_system_prompt(
        rubric=get_judge_rubric("outline_commercial"), methodology_reference="")
    assert "当前产物是写作规划，不是正文" in prompt
    assert "作者知道的信息自动归给当前视角角色" in prompt
    assert "表现形式本身，不构成违规" in prompt
    assert "黄金三章若只靠电话 / 短信 / 语音开局" not in prompt


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


def test_corpus_loader_returns_dedicated_corpus_when_available():
    corpus = J._load_reference_corpus("relationship-driven")
    assert corpus is not None
    assert corpus.get("genre") == "relationship-driven"


# ---------------------------------------------------------------------------
# F2 — chapter judge system prompt is genre-neutral
# ---------------------------------------------------------------------------


async def _capture_chapter_judge_request(monkeypatch, **kwargs):
    captured = []

    async def fake_complete_text(session, settings, request):
        captured.append(request)
        return LLMCompletionResult(
            content=json.dumps({
                "pass": False, "overall_score": 0.0, "dimension_scores": {},
                "blocking_issues": [], "rewrite_plan": {"scope": "chapter"},
            }),
            provider="mock", model_name="mock-critic",
        )

    monkeypatch.setattr(J, "complete_text", fake_complete_text)
    await J.judge_chapter_commercial_quality(
        object(), load_settings(env={}), chapter_number=1,
        content_md="主角核对记载后决定暂缓出发。", **kwargs,
    )
    assert len(captured) == 1
    return captured[0]


@pytest.mark.asyncio
async def test_chapter_judge_prompt_has_no_detective_leak_for_other_genre(monkeypatch):
    bible = {"power_system": {"terms": ["真元", "噬灵诀"]}}
    ctx = resolve_judge_genre_context(
        genre="玄幻", sub_genre="升级流",
        story_bible=bible,
    )
    request = await _capture_chapter_judge_request(
        monkeypatch,
        genre_context=ctx,
        generation_input={"schema_version": "chapter-generation-input.v2", "story_bible": bible},
    )
    for leak in _DETECTIVE_LEAK_TERMS:
        assert leak not in request.system_prompt + request.user_prompt
    # Book facts remain visible as source data, not system-level instructions.
    assert "真元" not in request.system_prompt
    assert "真元" in request.user_prompt
    assert "噬灵诀" in request.user_prompt
    assert "SOURCE AUTHORITY" in request.system_prompt


@pytest.mark.asyncio
async def test_chapter_judge_retains_source_grounded_decision_dimension_and_floor(monkeypatch):
    ctx = resolve_judge_genre_context(genre="仙侠", sub_genre="职业流")
    request = await _capture_chapter_judge_request(
        monkeypatch, genre_context=ctx,
    )

    assert "decision_intelligence" in request.user_prompt
    sent_dimensions = json.loads(request.user_prompt.split("关键维度：", 1)[1].splitlines()[0])
    _, expected_dimensions = J.chapter_commercial_thresholds(
        1, J._load_reference_corpus(ctx.corpus_key), request.metadata["writer_model"],
    )
    assert sent_dimensions["decision_intelligence"] == expected_dimensions["decision_intelligence"]
    assert sent_dimensions["decision_intelligence"] >= 0.84
    assert "人物动机、事件因果" in request.system_prompt
    assert "来源和正文证据" in request.system_prompt
    _, hard_dimensions = J.chapter_commercial_thresholds(1, None, None)
    assert hard_dimensions["decision_intelligence"] == 0.84
    _, later_dimensions = J.chapter_commercial_thresholds(80, None, None)
    assert later_dimensions["decision_intelligence"] == 0.84


@pytest.mark.asyncio
async def test_chapter_judge_runs_first_person_decision_intelligence_audit(monkeypatch):
    # Preserve the original semantic contract until its owner explicitly retires
    # it. A dimension name/floor alone does not prove the baseline audit survives.
    ctx = resolve_judge_genre_context(genre="仙侠", sub_genre="职业流")
    request = await _capture_chapter_judge_request(monkeypatch, genre_context=ctx)
    prompt = request.system_prompt + "\n" + request.user_prompt
    assert "decision_intelligence" in prompt
    assert "正常人基线" in prompt
    assert "角色基线" in prompt
    assert "PROTAGONIST_PLOT_SERVING_STUPIDITY" in prompt


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


def test_render_functions_without_context_use_neutral_fallback():
    # Legacy callers must not silently restore one genre's commission contract.
    rub = get_judge_rubric("outline_commercial")
    legacy = O._render_outline_commercial_system_prompt(
        rubric=rub, methodology_reference=""
    )
    assert "委托人选择动机" not in legacy


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
