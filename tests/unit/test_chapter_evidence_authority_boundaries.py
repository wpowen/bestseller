"""Local measurements propose evidence; only justified sources authorize edits."""
from __future__ import annotations

import copy
import json

import pytest

from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services.deduplication import (
    build_stitched_draft_repair_prompt,
    detect_intra_chapter_stitched_drafts,
)
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.opening_hook_density_gate import check_opening_hook_density
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("prop,location", [("暗格", "厨房"), ("令牌", "驿站"), ("玉简", "书房")])
def test_consecutive_scenes_are_candidates_not_proven_conflicts(prop, location):
    # Shared actors and props, then anticipated danger, then arrival: no reset.
    a = (f"叶宁把{prop}收好，顾安仍在{location}门外。他们先核对今天要用的东西，"
         "又约好等来人之后才动身。桌面已经收拾干净，窗前只剩下一张白纸。"
         "这时雨还没有停，顾安把湿外衣挂起来，叶宁腾出地方，请他坐下说话。")
    b = (f"叶宁想，如果有人推门看见顾安和{prop}，必定要追问事情的经过。"
         f"顾安却说先不要惊慌，{location}还有另一扇出口。他把凳子挪向窗边，"
         "然后压低声音说明来意。外面的脚步声逐渐接近，两人听见了熟悉的招呼，"
         "这才确定客人终于到了。")
    text = a + "\n\n---\n\n" + b
    assert detect_intra_chapter_stitched_drafts(text) == []
    candidates = detect_intra_chapter_stitched_drafts(
        text, name_pool=frozenset({"叶宁", "顾安"}), include_context_candidates=True,
    )
    assert candidates
    assert all(f.evidence_kind == "context_candidate" for f in candidates)
    assert all(f.requires_semantic_confirmation and not f.hard_gate for f in candidates)
    prompt = build_stitched_draft_repair_prompt(candidates)
    assert "不能直接判重复、矛盾或要求删段" in prompt
    assert "完整正文" in prompt
    assert "二选一" not in prompt and "禁止合并" not in prompt


@pytest.mark.parametrize("block", [
    "Mira checked the empty room, wrote the serial number and passed the form to Jonas. " * 4,
    "女医生核对病人的登记表，随后把文件交给值班护士。她看过最后一页才签下名字，窗外传来救护车的声音。" * 5,
])
def test_long_exact_blocks_gate_without_names_or_genre_props(block):
    pairs = detect_intra_chapter_stitched_drafts(block + "\n\n---\n\n" + block)
    assert len(pairs) == 1
    assert pairs[0].hard_gate and not pairs[0].requires_semantic_confirmation
    assert pairs[0].evidence_kind == "verbatim_repeated_block"
    assert "保留一份" in build_stitched_draft_repair_prompt(pairs)


def test_near_verbatim_is_not_byte_proof():
    block = "Jonas opened the door and found the sealed envelope on the desk. " * 5
    assert detect_intra_chapter_stitched_drafts(
        block + "\n\n---\n\n" + block.replace("sealed", "blue"),
    ) == []


def test_short_refrain_is_not_complete_long_block_proof():
    block = "人群再唱了一遍同样的歌，远处的钟声应和着熟悉的旋律。" * 4
    assert detect_intra_chapter_stitched_drafts(block + "\n\n---\n\n" + block) == []


LONG_OPENING = (
    "她把手中那张刚刚收到的书信放回桌上抬头望着窗外还在下雨的街道想起今天约定要见面的老朋友。"
    "顾安记得以前的事，当年的回忆里他曾经来过这里。"
    "叶宁把收好的纸递给他，随后到门口等客人。" * 3
)


def test_default_opening_metrics_and_legacy_knobs_never_authorize_veto():
    findings = check_opening_hook_density(
        LONG_OPENING, 1, first_sentence_max_cjk=10, flashback_max=0,
    )
    assert {"OPENING_FIRST_SENTENCE_TOO_LONG", "OPENING_FLASHBACK_OVERUSE"} <= {f.code for f in findings}
    assert all(f.severity not in {"high", "critical"} for f in findings)
    assert all(f.evidence.get("applicability") == "reference_only"
               for f in findings if f.code != "OPENING_NO_ANOMALY")


@pytest.mark.parametrize("limit", [True, "25", 0, -1, None])
def test_untyped_or_invalid_contract_does_not_create_hard_limit(limit):
    findings = check_opening_hook_density(LONG_OPENING, 1, approved_opening_contract={
        "source_requirement": "Approved current chapter clause", "first_sentence_max_cjk": limit,
    })
    assert all(f.severity not in {"high", "critical"} for f in findings)


@pytest.mark.parametrize("requirement", [None, "", " ", True])
def test_limit_without_source_clause_is_advisory(requirement):
    findings = check_opening_hook_density(LONG_OPENING, 1, approved_opening_contract={
        "source_requirement": requirement, "first_sentence_max_cjk": 10,
    })
    assert all(f.severity not in {"high", "critical"} for f in findings)


@pytest.mark.parametrize("chapter", [1, 12])
def test_explicit_current_limit_applies_without_default_golden_three_scope(chapter):
    contract = {"source_requirement": "Creator approved: current opening at most ten CJK characters.",
                "first_sentence_max_cjk": 10}
    before = copy.deepcopy(contract)
    findings = check_opening_hook_density("这是一句明确超出了十个汉字的开头。", chapter,
                                          approved_opening_contract=contract)
    sentence = next(f for f in findings if f.code == "OPENING_FIRST_SENTENCE_TOO_LONG")
    assert sentence.severity == "high"
    assert sentence.evidence["source_requirement"] == contract["source_requirement"]
    assert sentence.evidence["applicability"] == "approved_contract"
    assert contract == before


def test_explicit_limit_replaces_reference_number():
    findings = check_opening_hook_density(LONG_OPENING, 1, approved_opening_contract={
        "source_requirement": "Creator allows an opening up to 100 CJK characters.",
        "first_sentence_max_cjk": 100,
    })
    assert "OPENING_FIRST_SENTENCE_TOO_LONG" not in {f.code for f in findings}
    assert all(f.severity != "high" for f in findings)


@pytest.mark.asyncio
@pytest.mark.parametrize("language,corpus", [("zh-CN", "action-progression"),
                                            ("en", "relationship-driven")])
async def test_actual_judge_prompt_marks_history_untrusted_but_retains_contract(monkeypatch, language, corpus):
    captured = []
    raw = json.dumps({"pass": False, "overall_score": 0.51, "dimension_scores": {},
                      "blocking_issues": [{"code": "REAL_SOURCE_CONFLICT", "severity": "high",
                                           "evidence": "A factual contradiction.", "required_fix": "Review source."}],
                      "audit_issues": [], "rewrite_plan": {"scope": "chapter"}})

    async def capture(session, settings, request):
        captured.append(request)
        return LLMCompletionResult(content=raw, provider="mock", model_name="mock-critic")

    monkeypatch.setattr(judge, "complete_text", capture)
    source = {
        "prior_write_safety": {"code": "OPENING_FIRST_SENTENCE_TOO_LONG", "message": "25 字硬上限"},
        "approved_creation_contracts": {"user_effect": "Deliver a small practical success promptly."},
        "approved_chapter_contract": {
            "opening": "Explicitly approved: opening sentence may be 80 characters.",
            "tail_hook": {"source": "ai_generated", "status": "proposal",
                          "text": "The delivery remains unfinished at the end."},
            "explicit_hard_clause": {"hard": True, "approved_by": "user",
                                     "text": "Do not reveal the sealed letter's contents yet."},
        },
        "approved_world_authority": {"authority_status": "current",
                                     "ability_limit": "The courier cannot read minds."},
        "occurred_canon": "The recipient has already received the parcel.",
    }
    before = copy.deepcopy(source)
    result = await judge.judge_chapter_commercial_quality(
        object(), load_settings(env={}), chapter_number=1, content_md=LONG_OPENING,
        generation_input=source, language=language, reference_corpus_key=corpus,
    )
    request = captured[0]
    assert "Numeric opening limits require an explicitly approved current clause and source" in request.system_prompt
    assert "旧报告及旧整改建议均不构成授权" in request.system_prompt
    assert "editorial plan variance" in request.system_prompt
    assert "不得仅因偏离而 blocking" in request.system_prompt
    assert "Established factual constraints remain hard without a hard label" in request.system_prompt
    assert "AI 提案不自动成为硬前提" in request.system_prompt
    assert "与生成输入前提冲突 → 必判 blocking" not in request.system_prompt
    assert "80 characters" in request.user_prompt
    assert "Deliver a small practical success promptly" in request.user_prompt
    assert "Do not reveal the sealed letter" in request.user_prompt
    assert "cannot read minds" in request.user_prompt
    assert "has already received the parcel" in request.user_prompt
    assert "25 字硬上限" in request.user_prompt  # retain history; don't forge or erase it
    assert request.prompt_version == "v4-source-bound-review"
    assert "测灵石第三次没有亮" not in request.system_prompt
    assert "本章须兑现一个打脸" not in request.system_prompt
    assert "参考样本水准" not in request.system_prompt
    assert result.raw_excerpt == raw and not result.passed
    assert source == before
