"""Lexical motif detectors may diagnose text, but may not choose the story."""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest

from bestseller.services import conception
from bestseller.services.blurb_copywriter import (
    BlurbCandidate,
    demote_default_family_candidates,
    run_blurb_copywriting,
)
from bestseller.services.title_tournament import (
    TitleCandidate,
    adjudicate_late_title,
    demote_default_family_titles,
    select_title_winner,
)

pytestmark = pytest.mark.unit


_DEBT_TITLE = "我替全宗门还灵债"
_CLEAN_TITLE = "三息辨火"
_DEBT_BLURB = (
    "顾行接下第一笔借款，账本立刻写明到期日。到期清偿后，他才能再借一笔；"
    "每次偿还都会让下一位债权人提高条件，新的债务冲突随之升级。"
)
_CLEAN_BLURB = "顾行能从炉灰里听出火候，三息内救回即将熄灭的药炉。"


def test_title_lexical_hit_is_audited_without_changing_membership_or_winner() -> None:
    lexical = TitleCandidate(title=_DEBT_TITLE, family="promise", arena_picks=4)
    clean = TitleCandidate(title=_CLEAN_TITLE, family="object", arena_picks=1)

    eligible, advisory = demote_default_family_titles(
        [lexical, clean], user_named_family=False
    )

    assert eligible == [lexical, clean]
    assert advisory == [lexical]
    assert select_title_winner(eligible) is lexical


def test_late_title_lexical_hit_cannot_veto_an_eligible_challenger() -> None:
    adopted, receipt = adjudicate_late_title(
        _CLEAN_TITLE,
        _DEBT_TITLE,
        prose="顾行替全宗门承接灵债，以清偿顺序换取各峰支持。",
    )

    assert adopted == _DEBT_TITLE
    challenger = next(
        row for row in receipt["candidates"] if row["title"] == _DEBT_TITLE
    )
    assert challenger["default_family_observed"] is True
    assert challenger["default_family_blocking"] is False
    assert challenger["default_family_selection_changed"] is False
    assert "default_family_demoted" not in challenger


def test_blurb_lexical_hit_is_audited_without_changing_ranked_membership() -> None:
    lexical = BlurbCandidate(
        strategy="debt_cycle",
        synopsis=_DEBT_BLURB,
        persona_avg_score=9.0,
        gate_score=88.0,
    )
    clean = BlurbCandidate(
        strategy="fire",
        synopsis=_CLEAN_BLURB,
        persona_avg_score=4.0,
        gate_score=70.0,
    )

    eligible, advisory = demote_default_family_candidates(
        [lexical, clean], user_named_family=False
    )

    assert eligible == [lexical, clean]
    assert advisory == [lexical]
    assert max(
        eligible,
        key=lambda row: (row.persona_avg_score or -1.0, row.gate_score or 0.0),
    ) is lexical


@pytest.mark.asyncio
async def test_blurb_tournament_keeps_highest_ranked_lexical_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bestseller.services import (
        blurb_appeal_gate,
        blurb_coherence_judge,
        blurb_copywriter,
    )

    responses = iter((_DEBT_BLURB, _CLEAN_BLURB))

    async def generator(_system: str, _user: str):
        return json.dumps({"synopsis": next(responses)}, ensure_ascii=False), None

    async def judge(_system: str, user: str) -> str:
        score = 9.0 if _DEBT_BLURB in user else 4.0
        return json.dumps({"click": True, "score": score, "reason": "ok"})

    async def coherent(*_args, **_kwargs):
        return blurb_coherence_judge.CoherenceReport(findings=(), llm_used=True)

    monkeypatch.setattr(blurb_copywriter, "detect_blurb_pathology", lambda *_a, **_k: [])
    monkeypatch.setattr(
        blurb_copywriter, "detect_ungrounded_blurb_claims", lambda *_a, **_k: []
    )
    monkeypatch.setattr(
        blurb_appeal_gate,
        "evaluate_blurb_appeal",
        lambda **_kwargs: SimpleNamespace(total=80.0, grade="pass"),
    )
    monkeypatch.setattr(blurb_coherence_judge, "verify_blurb_coherence", coherent)

    result = await run_blurb_copywriting(
        None,
        None,
        spine={},
        premise="顾行经营宗门资源周转。",
        golden_finger_line="能看见契约到期顺序。",
        title="顾行的契约",
        tags=[],
        genre="玄幻",
        sub_genre="宗门经营",
        platform=None,
        language="zh-CN",
        v0_synopsis="顾行必须救活宗门。",
        config={
            "copywriting": {
                "n_candidates": 2,
                "persona_samples": 1,
                "max_polish_rounds": 0,
            }
        },
        generator=generator,
        persona_judge=judge,
    )

    assert result.champion == _DEBT_BLURB
    assert result.default_family == {
        "scanned": True,
        "observed_strategies": ["scene_hook"],
        "user_requested": False,
        "policy": "advisory_only",
        "blocking": False,
        "selection_changed": False,
    }
    assert result.to_dict()["default_family"] == result.default_family


@pytest.mark.asyncio
async def test_wired_title_tournament_keeps_lexical_candidate_eligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fake_llm_call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return (
                json.dumps(
                    {
                        "candidates": [
                            {"title": _DEBT_TITLE, "family": "promise"},
                            {"title": _CLEAN_TITLE, "family": "object"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                None,
            )
        return json.dumps({"pick": 1, "reject": 2}, ensure_ascii=False), None

    monkeypatch.setattr(conception, "_llm_call", fake_llm_call)
    monkeypatch.setattr(
        conception,
        "evaluate_platform_title_candidate",
        lambda *_args, **_kwargs: {"decision": "pass"},
    )

    winner, receipt, _run_ids = await conception._run_title_tournament(
        None,
        None,
        incumbent="",
        title_profile={
            "main_characters": [{"name": "顾行"}],
            "logline": "顾行替全宗门承接灵债，也能在三息内辨出炉火。",
            "short_intro": "他以清偿顺序换取各峰支持。",
            "reader_promise": "宗门经营会随契约条件变化。",
            "tags": [],
        },
        golden_finger="看见契约到期顺序",
        logline="顾行替全宗门承接灵债，也能在三息内辨出炉火。",
        genre_label="玄幻",
        platform_label="番茄小说",
        is_en=False,
    )

    assert winner == _DEBT_TITLE
    assert receipt is not None
    assert receipt["default_family"] == {
        "scanned": True,
        "observed": [_DEBT_TITLE],
        "user_requested": False,
        "policy": "advisory_only",
        "blocking": False,
        "selection_changed": False,
    }
    assert receipt["rejected"] == []


def test_user_named_family_is_scanned_without_policy_action() -> None:
    adopted, receipt = adjudicate_late_title(
        _CLEAN_TITLE,
        _DEBT_TITLE,
        prose="顾行替全宗门承接灵债，以清偿顺序换取各峰支持。",
        user_named_default_family=True,
    )

    assert adopted == _DEBT_TITLE
    assert receipt["default_family"] == {
        "scanned": True,
        "observed": [_DEBT_TITLE],
        "user_requested": True,
        "policy": "advisory_only",
        "blocking": False,
        "selection_changed": False,
    }


@pytest.mark.parametrize("with_motif", [False, True])
def test_debt_signal_cannot_authorize_finalize_retry(with_motif: bool) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    motif_hits = [object()] if with_motif else []

    authorized = conception._mechanism_retry_gate(
        motif_hits=motif_hits,
        echo_report=[],
        debt_hit=True,
        death_hit=False,
        ontology_hit=(),
        intent_violations=(),
        heavy_tone_hits=(),
        emit=lambda stage, payload: events.append((stage, payload)),
    )

    assert authorized is False
    assert events[0][1]["mode"] == "audit_only"
    assert events[0][1]["retry_started"] is False


def test_only_verbatim_cross_book_echo_or_explicit_intent_authorizes_retry() -> None:
    weak_echo = [{"shared_span": "", "shared_bigrams": ["旧账", "宗门"]}]
    hard_echo = [{"shared_span": "灵根枯竭的底层杂役", "shared_bigrams": []}]

    assert not conception._mechanism_retry_has_non_motif_trigger(
        echo_report=weak_echo,
        debt_hit=True,
        death_hit=False,
        ontology_hit=(),
        intent_violations=(),
        heavy_tone_hits=(),
    )
    assert conception._mechanism_retry_has_non_motif_trigger(
        echo_report=hard_echo,
        debt_hit=False,
        death_hit=False,
        ontology_hit=(),
        intent_violations=(),
        heavy_tone_hits=(),
    )


def test_finalize_does_not_inject_lexical_replacement_feedback() -> None:
    source = inspect.getsource(conception.run_conception_pipeline)

    assert "_render_debt_rewrite_feedback(" not in source
    assert "_render_motif_amplification_feedback(" not in source


def test_mixed_authoritative_retry_does_not_smuggle_weak_echo_into_feedback() -> None:
    weak_echo = [{"shared_span": "", "shared_bigrams": ["旧账", "宗门"]}]
    hard_echo = [{"shared_span": "灵根枯竭的底层杂役", "shared_bigrams": []}]

    # The returned value is the exact user request passed to the retry LLM.
    # An explicit intent violation remains actionable, while the weak lexical
    # echo cannot smuggle in its own forced replacement instruction.
    prompt = conception._build_authoritative_mechanism_retry_prompt(
        "CLEAN PROMPT",
        echo_report=weak_echo,
        heavy_tone_hits=(),
        death_hit=False,
        ontology_hit=(),
        intent_violations=("用户明确要求轻松基调，成稿却持续沉重",),
        is_en=False,
    )
    assert "严格兑现建书选项" in prompt
    assert "跨书相似度门" not in prompt
    assert "默认主题族收敛" not in prompt

    hard_prompt = conception._build_authoritative_mechanism_retry_prompt(
        "CLEAN PROMPT",
        echo_report=hard_echo,
        heavy_tone_hits=(),
        death_hit=False,
        ontology_hit=(),
        intent_violations=(),
        is_en=False,
    )
    assert "跨书相似度门" in hard_prompt
    assert conception._mechanism_retry_has_non_motif_trigger(
        echo_report=[],
        debt_hit=False,
        death_hit=False,
        ontology_hit=(),
        intent_violations=("用户明确要求轻松基调，成稿却持续沉重",),
        heavy_tone_hits=(),
    )
