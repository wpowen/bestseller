import json

import pytest

from bestseller.services import concept_tournament as tournament
from bestseller.services.concept_tournament import (
    ConceptCandidate,
    _audit_causal_evidence,
    _build_raw_idea_rank_messages,
    _seriality_audit,
    _parse_raw_idea_ranking,
    _select_raw_ideas_for_expansion,
)
from bestseller.services.ranking_seriality_gate import (
    required_ranking_unit_count,
)


def _rank_item(**overrides):
    item = {
        "index": 0,
        "freshness": 8,
        "click_seed": 8,
        "character_logic": 8,
        "action_seed": 8,
        "promise_survival": 8,
        "genre_fidelity": 8,
        "ai_assembly": 1,
        "dumb_cost": False,
        "after_opening_promise": "她必须查清账目并保护妹妹",
        "action_families": ["查账", "谈判", "保护"],
        "growth_surface": "信任、现金流与家庭关系持续变化",
        "causal_evidence_status": "verified",
    }
    item.update(overrides)
    return item


def test_causal_quote_must_exist_in_complete_candidate_support():
    item = {
        "causal_evidence": [
            {
                "source_quote": "她是唯一能调取账目的人",
                "assumed_link": "所以她选择查账",
                "simpler_alternative": "可以报警，但无法先保住妹妹",
                "motivation_justification": "妹妹会先被带走",
                "verified": True,
            }
        ]
    }
    assert _audit_causal_evidence(item, "她负责小店收银，妹妹被债主带走。") == (
        False,
        "causal_evidence_unverified",
    )
    assert _audit_causal_evidence(
        item, "她是唯一能调取账目的人；妹妹被债主带走。"
    ) == (True, "verified")


def test_causal_audit_requires_json_true_and_every_record_to_verify():
    base = {
        "source_quote": "她调账",
        "assumed_link": "发现缺口",
        "simpler_alternative": "报警",
        "motivation_justification": "妹妹受威胁",
        "verified": True,
    }
    string_false = dict(base, verified="false")
    assert _audit_causal_evidence({"causal_evidence": [string_false]}, "她调账") == (
        False,
        "causal_evidence_unverified",
    )
    incomplete = dict(base, source_quote="不存在的原文")
    assert _audit_causal_evidence(
        {"causal_evidence": [base, incomplete]}, "她调账"
    ) == (False, "causal_evidence_unverified")


def test_parser_preserves_bounded_causal_audit_fields():
    parsed = _parse_raw_idea_ranking(
        '{"ranked":[{"index":0,"freshness":8,"click_seed":8,"character_logic":8,'
        '"action_seed":8,"promise_survival":8,"genre_fidelity":8,"ai_assembly":1,'
        '"causal_evidence":[{"source_quote":"她调账","assumed_link":"发现缺口",'
        '"simpler_alternative":"报警","motivation_justification":"妹妹受威胁",'
        '"verified":true}]}]}'
    )
    assert parsed[0]["causal_evidence"][0]["source_quote"] == "她调账"
    assert parsed[0]["causal_evidence"][0]["verified"] is True


def test_causal_audit_rejects_non_string_evidence_fields():
    parsed = _parse_raw_idea_ranking(
        '{"ranked":[{"index":0,"freshness":8,"click_seed":8,"character_logic":8,'
        '"action_seed":8,"promise_survival":8,"genre_fidelity":8,"ai_assembly":1,'
        '"causal_evidence":[{"source_quote":{"text":"她调账"},"assumed_link":["发现缺口"],'
        '"simpler_alternative":"报警","motivation_justification":"妹妹受威胁",'
        '"verified":true}]}]}'
    )
    assert _audit_causal_evidence(parsed[0], "她调账发现缺口") == (
        False,
        "causal_evidence_unverified",
    )


def test_missing_causal_evidence_cannot_enter_production_selection():
    selected = _select_raw_ideas_for_expansion(
        [_rank_item(causal_evidence_status="invalid", causal_evidence_reason="causal_evidence_missing")],
        raw_floor=7,
        progression_floor=5,
        limit=1,
    )
    assert selected == []


def test_rank_prompt_requires_quote_bound_causal_audit_and_retry_feedback():
    system, user = _build_raw_idea_rank_messages(
        genre="现实百态",
        sub_genre="现实百态",
        ideas=[("职业困境", "她负责小店收银，妹妹被债主带走")],
        pitch_by_seed={
            "她负责小店收银，妹妹被债主带走": {
                "graft": "她调账发现缺口",
                "opening": "妹妹失联",
                "why_it_keeps_moving": "她必须查账",
                "future_situations": ["谈判", "保护妹妹", "修复关系"],
            }
        },
        retry_feedback="候选序号 0 的 causal_evidence 缺失",
    )
    assert "causal_evidence" in user
    assert "source_quote" in user
    assert "候选序号 0" in user
    assert "严苛" in system


@pytest.mark.asyncio
async def test_production_rank_path_retries_invalid_causal_audit(monkeypatch):
    rank_calls = []
    progress_events = []

    monkeypatch.setattr(
        tournament,
        "emit_activity",
        lambda event, payload: progress_events.append((event, payload)),
    )

    async def fake_default_generator(_session, _settings, *, template, **kwargs):
        del kwargs

        async def call(_system, user):
            if template == "concept_tournament_raw_idea_pool":
                return json.dumps(
                    {"ideas": [{"lane": "现实", "seed": "她调账发现缺口"},
                               {"lane": "现实", "seed": "她谈判保住妹妹"}]},
                    ensure_ascii=False,
                ), None
            if template == "concept_tournament_raw_idea_rank":
                rank_calls.append(user)
                rows = []
                for index, seed in enumerate(("她调账发现缺口", "她谈判保住妹妹")):
                    rows.append({
                        "index": index,
                        "freshness": 8,
                        "click_seed": 8,
                        "character_logic": 8,
                        "action_seed": 8,
                        "promise_survival": 8,
                        "genre_fidelity": 8,
                        "ai_assembly": 1,
                        "after_opening_promise": "她必须查清账目并保护妹妹",
                        "action_families": ["查账", "谈判", "保护"],
                        "growth_surface": "信任、现金流与家庭关系持续变化",
                        "causal_evidence": [{
                            "source_quote": seed,
                            "assumed_link": "处境迫使她行动",
                            "simpler_alternative": "可以报警但无法立即保护妹妹",
                            "motivation_justification": "她必须先保护妹妹",
                            "verified": False if len(rank_calls) == 1 else True,
                        }],
                    })
                return json.dumps({"ranked": rows}, ensure_ascii=False), None
            return json.dumps({"concept": "她查账保住妹妹"}, ensure_ascii=False), None

        return call

    monkeypatch.setattr(tournament, "_default_generator", fake_default_generator)
    result = await tournament.run_concept_tournament(
        None,
        None,
        genre="现实",
        sub_genre="生活纪实",
        chapter_count=20,
        config={
            "enabled": True,
            "n_candidates": 2,
            "candidate_prompt_mode": "engine_first",
            "raw_idea_prompt_arm": "methodology",
            "raw_idea_pool_multiplier": 1,
            "raw_idea_generation_batch_size": 2,
            "raw_idea_pool_topup_calls": 0,
            "raw_idea_rank_batch_size": 8,
            "raw_idea_rank_max_tokens": 3000,
            "finalist_judge_model_key": "test-ranker",
            "premise_card_count": 1,
        },
    )
    assert len(rank_calls) == 2
    assert "causal_evidence 缺失" in rank_calls[1]
    assert result.raw_idea_rank_coverage["retry_calls"] == 1
    assert any(item.get("causal_evidence_status") == "verified" for item in result.raw_idea_ranking)
    retry_events = [
        payload
        for event, payload in progress_events
        if event == "concept_tournament_progress"
        and payload.get("stage") == "raw_idea_rank_causal_review"
    ]
    assert [payload["status"] for payload in retry_events] == ["started", "completed"]
    assert all(payload["review_kind"] == "causal_evidence_retry" for payload in retry_events)
    assert all(payload["generated_candidates"] is False for payload in retry_events)


@pytest.mark.asyncio
async def test_causal_review_retry_failure_emits_failed_progress(monkeypatch):
    events = []
    rank_calls = 0
    monkeypatch.setattr(
        tournament,
        "emit_activity",
        lambda event, payload: events.append((event, payload)),
    )

    async def fake_default_generator(_session, _settings, *, template, **kwargs):
        del kwargs

        async def call(_system, _user):
            nonlocal rank_calls
            if template == "concept_tournament_raw_idea_pool":
                return json.dumps(
                    {"ideas": [{"lane": "现实", "seed": "她调账发现缺口"}]},
                    ensure_ascii=False,
                ), None
            if template == "concept_tournament_raw_idea_rank":
                rank_calls += 1
                if rank_calls > 1:
                    raise RuntimeError("review unavailable")
                return json.dumps(
                    {"ranked": [{"index": 0, "causal_evidence": []}]},
                    ensure_ascii=False,
                ), None
            return json.dumps({"concept": "她查账保住妹妹"}, ensure_ascii=False), None

        return call

    monkeypatch.setattr(tournament, "_default_generator", fake_default_generator)
    await tournament.run_concept_tournament(
        None,
        None,
        genre="现实",
        sub_genre="生活纪实",
        chapter_count=20,
        config={
            "enabled": True,
            "n_candidates": 1,
            "candidate_prompt_mode": "engine_first",
            "raw_idea_prompt_arm": "methodology",
            "raw_idea_pool_multiplier": 1,
            "raw_idea_generation_batch_size": 1,
            "raw_idea_pool_topup_calls": 0,
            "raw_idea_rank_batch_size": 8,
            "finalist_judge_model_key": "test-ranker",
            "premise_card_count": 1,
        },
    )
    retry_events = [
        payload
        for event, payload in events
        if event == "concept_tournament_progress"
        and payload.get("stage") == "raw_idea_rank_causal_review"
    ]
    assert [payload["status"] for payload in retry_events] == ["started", "failed"]
    assert retry_events[-1]["generated_candidates"] is False


@pytest.mark.asyncio
async def test_production_seriality_repair_raises_50_chapter_budget_to_13():
    candidate_payload = {
        "concept": "小店收银员查出妹妹被债主带走的账目漏洞，只能在今晚清算前用一笔笔真实流水把她带回来",
        "mechanism": "每次查账都会暴露新的关系与责任",
        "hook_question": "她能否在账目清算前保住妹妹？",
        "protagonist_identity": "小店收银员",
        "protagonist_private_desire": "保住妹妹",
        "protagonist_flaw": "过度相信账目",
        "core_abnormality": "她能看见账目缺口",
        "opening_crisis": "妹妹被债主带走",
        "opponent_system": "债主与亲属",
        "decision_proof": "报警来不及，她选择先查账",
        "emotional_promise": "用专业能力保住家人",
    }
    prompts: list[str] = []
    calls = 0

    async def generator(_system, _user):
        return json.dumps(candidate_payload, ensure_ascii=False), None

    async def judge(_system, _user):
        return json.dumps({
            "freshness": 8, "click": 8, "predictable": 2,
            "character_logic": 8, "mechanism_causality": 8,
            "genre_fidelity": 8, "plain_language": 8, "story_motion": 8,
            "reason": "test",
        }, ensure_ascii=False), None

    async def expander(_system, user):
        nonlocal calls
        calls += 1
        prompts.append(user)
        count = 12 if calls == 1 else 13
        cadence = "每10章一次" if calls == 1 else "每2-4章一次"
        return json.dumps({
            "core_promise_invariant": "她用查账保住妹妹并修复关系",
            "repeatable_story_unit": "每次查账都会暴露新的关系与责任",
            "unit_families": ["查账", "谈判", "保护", "修复"],
            "progress_bar": "信任与现金流持续变化",
            "unit_frequency": cadence,
            "unit_count_estimate": count,
            "renewal_sources": ["账目缺口", "关系反制"],
            "accumulation_tracks": ["现金流", "信任"],
            "phase_transitions": ["第1-20章", "第21-35章", "第36-50章"],
            "opposing_ecology": ["债主", "亲属"],
            "forward_question_ladder": ["谁改账", "谁反制", "如何保住妹妹"],
            "ch50": "她面对新的账目反制",
            "endgame_direction": "在目标篇幅内修复账目和关系",
        }, ensure_ascii=False), None

    result = await tournament.run_concept_tournament(
        None,
        None,
        genre="现实",
        sub_genre="生活纪实",
        chapter_count=50,
        config={
            "enabled": True,
            "n_candidates": 2,
            "candidate_prompt_mode": "current",
            "seriality_advisory_min_chapters": 8,
            "premise_card_count": 1,
        },
        generator=generator,
        judge=judge,
        expander=expander,
    )
    assert calls == 2
    assert any(
        token in prompts[1]
        for token in ("unit_count_below_minimum", "unit_frequency_unproven_or_sparse")
    )
    assert "修复：" in prompts[1]
    assert "不少于13" in prompts[0]
    assert result.winner is not None


def _seriality_candidate(unit_count: int) -> ConceptCandidate:
    return ConceptCandidate(
        dimension="现实",
        concept="她查账保住妹妹",
        core_promise_invariant="她用查账保住妹妹并修复关系",
        repeatable_story_unit="每次查账都会暴露新的关系与责任",
        progress_bar="小店账目与家庭关系持续变化",
        question_ladder=("下一笔账是谁改的", "谁会反制", "她要保住什么"),
        endgame_direction="在目标篇幅内修复账目和关系",
        ch50="她在第50章面对新的账目反制",
        unit_count_estimate=unit_count,
        unit_frequency="每2-4章一次",
        unit_families=("查账", "谈判", "保护", "修复"),
        renewal_sources=("账目缺口", "关系反制"),
        accumulation_tracks=("现金流", "信任"),
        phase_transitions=("第1-20章", "第21-35章", "第36-50章"),
        opposing_ecology=("债主", "亲属"),
    )


def test_ranking_seriality_threshold_is_shared_across_30_50_200():
    assert required_ranking_unit_count(30) == 8
    assert required_ranking_unit_count(50) == 13
    assert required_ranking_unit_count(200) == 50


def test_seriality_repair_audit_reports_ranking_gate_before_contract():
    finding = _seriality_audit(_seriality_candidate(12), target_chapters=50)
    assert finding is not None
    assert "unit_count_below_minimum" in finding
    assert _seriality_audit(_seriality_candidate(13), target_chapters=50) is None
    epic_finding = _seriality_audit(_seriality_candidate(49), target_chapters=200)
    assert epic_finding is not None
    assert "unit_count_below_minimum" in epic_finding
