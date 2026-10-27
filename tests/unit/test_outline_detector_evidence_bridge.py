"""Current-source detector quotes survive compacting without becoming authority."""

from copy import deepcopy
import hashlib
import json

import pytest

from bestseller.services import outline_llm_judge as judge
from bestseller.services.llm import LLMCompletionResult
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def candidate():
    return {
        "batch_name": "current-window",
        "_meta": {"old_receipt": "not current chapter evidence"},
        "chapters": [
            {
                "chapter_number": 7,
                "goal": "Reach the pier",
                "decision_protocol": {"personality_basis": "She remains 'faithful to her craft'."},
                "selected_effect_skills": {
                    "contract": "The regulation says 'licensed boats only'."
                },
            },
            {
                "chapter_number": 8,
                "goal": "Keep the ferry",
                "decision_protocol": {
                    "personality_basis": "Again 'faithful to her craft' under pressure."
                },
            },
        ],
    }


def probes():
    return [
        {
            "code": "REUSED_ANCHOR",
            "chapter": 7,
            "evidence": {"anchor": "faithful to her craft", "chapters": [7, 8]},
        },
        {"code": "MISSING_LITERAL", "chapter": 8, "evidence": {"matches": ["licensed boats only"]}},
        {"code": "NO_PROBE", "chapter": 7, "evidence": {"field": "goal"}},
    ]


def test_bridge_finds_dropped_fields_at_exact_current_json_pointers_without_mutation():
    outline, findings = candidate(), probes()
    original = deepcopy((outline, findings))
    bridge = judge.build_outline_detector_evidence(outline, findings)
    first, missing, unsupported = bridge["findings"]
    assert first["status"] == "found"
    assert [q["path"] for q in first["quotes"]] == [
        "/chapters/0/decision_protocol/personality_basis",
        "/chapters/1/decision_protocol/personality_basis",
    ]
    assert all(not q["compact_path_present"] for q in first["quotes"])
    assert all("faithful to her craft" in q["quote"] for q in first["quotes"])
    assert missing["status"] == "not_found" and missing["quotes"] == []
    assert unsupported["status"] == "no_literal_probe"
    assert bridge["diagnostic_only"] and not bridge["setting_authority"]
    assert (outline, findings) == original


def test_current_body_hash_excludes_only_top_meta_and_changes_with_source_body():
    outline = candidate()
    bridge = judge.build_outline_detector_evidence(outline, probes())
    body = {key: value for key, value in outline.items() if key != "_meta"}
    expected = hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()
    assert bridge["current_body_hash"] == expected
    assert bridge["hash_scope"] == "outline_payload_without_top_level__meta"
    outline["_meta"]["old_receipt"] = "different"
    assert judge.build_outline_detector_evidence(outline, probes())["current_body_hash"] == expected
    outline["chapters"][0]["_meta"] = {"nested": "included in the body hash"}
    assert judge.build_outline_detector_evidence(outline, probes())["current_body_hash"] != expected


def test_visible_literal_and_dropped_literal_remain_distinct_from_violation_verdict():
    outline = candidate()
    outline["chapters"][0]["event_cycle_contract"] = {"new_state": "白天归宗待定"}
    findings = [{"code": "META_LANGUAGE", "chapter": 7, "evidence": {"matches": ["待定"]}}]
    bridge = judge.build_outline_detector_evidence(outline, findings)
    (quote,) = bridge["findings"][0]["quotes"]
    assert quote["compact_path_present"] and quote["compact_literal_present"]
    assert quote["quote"] == "白天归宗待定"
    assert bridge["findings"][0]["status"] == "found"
    assert "violation" not in bridge["findings"][0]


def test_missing_compact_literal_due_to_truncation_is_not_missing_source_literal():
    outline = {"chapters": [{"chapter_number": 3, "goal": "x" * 200 + "current evidence"}]}
    findings = [{"code": "LONG_FIELD", "chapter": 3, "evidence": {"anchor": "current evidence"}}]
    result = judge.build_outline_detector_evidence(outline, findings)["findings"][0]
    (quote,) = result["quotes"]
    assert result["status"] == "found"
    assert quote["compact_path_present"] and not quote["compact_literal_present"]
    assert "current evidence" in quote["quote"]


def test_quote_caps_and_context_are_bounded_without_omitting_literal():
    outline = {
        "chapters": [
            {"chapter_number": i, "extra": ["a" * 400 + "needle" + "z" * 400] * 9}
            for i in range(1, 9)
        ]
    }
    findings = [{"code": "MANY", "evidence": {"anchor": "needle", "chapters": list(range(1, 9))}}]
    item = judge.build_outline_detector_evidence(outline, findings)["findings"][0]
    assert item["status"] == "found" and len(item["quotes"]) == 12
    for chapter in range(1, 9):
        assert sum(q["chapter_number"] == chapter for q in item["quotes"]) <= 3
    assert all(len(q["quote"]) <= 240 and "needle" in q["quote"] for q in item["quotes"])
    assert item["quotes_truncated"]


def test_exact_literal_search_never_uses_regex_root_meta_or_unrequested_chapters():
    outline = candidate()
    outline["_meta"]["receipt"] = "root-only"
    findings = [
        {"code": "ROOT", "chapter": 7, "evidence": {"anchor": "root-only"}},
        {"code": "REGEX", "chapter": 7, "evidence": {"anchor": ".*"}},
        {"code": "WRONG_CHAPTER", "chapter": 99, "evidence": {"anchor": "faithful to her craft"}},
    ]
    result = judge.build_outline_detector_evidence(outline, findings)
    assert all(item["status"] == "not_found" for item in result["findings"])


def test_json_pointer_escapes_field_names_and_finding_count_is_bounded():
    outline = {"chapters": [{"chapter_number": 1, "a/b~c": "source needle"}]}
    findings = [{"code": "BOUNDED", "chapter": 1, "evidence": {"anchor": "needle"}}] * 30
    result = judge.build_outline_detector_evidence(outline, findings)
    assert len(result["findings"]) <= 12
    assert result["findings"][0]["quotes"][0]["path"] == "/chapters/0/a~1b~0c"
    assert result["findings_truncated"]


@pytest.mark.asyncio
async def test_judge_receives_bridge_outside_authority_even_when_optional_brief_is_oversized(
    monkeypatch,
):
    outline = candidate()
    brief = {
        "oversized_optional": "x" * 10000,
        "deterministic_semantic_candidates": probes(),
        "approved_world_authority": {
            "authority_status": "current",
            "world_spec": {"rule": "locked"},
        },
        "canonical_protagonist": {"name": "Una"},
    }
    original = deepcopy((outline, brief))
    requests = []

    async def fake_complete(session, settings, request):
        requests.append(request)
        return LLMCompletionResult(
            content=json.dumps(
                {
                    "pass": True,
                    "overall_score": 0.9,
                    "blocking_issues": [],
                    "dimension_scores": {},
                }
            ),
            provider="mock",
            model_name="fixture",
        )

    monkeypatch.setattr(judge, "complete_text", fake_complete)
    await judge.judge_outline_commercial_readiness(
        None,
        load_settings(env={}),
        outline_payload=outline,
        project_brief=brief,
    )
    assert len(requests) == 1
    prompt = requests[0].user_prompt
    assert '"diagnostic_evidence"' in prompt
    assert "/chapters/0/decision_protocol/personality_basis" in prompt
    assert '"status": "found"' in prompt
    assert '"status": "not_found"' in prompt
    assert '"status": "no_literal_probe"' in prompt
    authority = prompt.split("[SOURCE AUTHORITY]", 1)[1].split("[/SOURCE AUTHORITY]", 1)[0]
    assert "diagnostic_evidence" not in authority and "locked" in authority
    assert judge.OUTLINE_SOURCE_AUTHORITY_RULES in requests[0].system_prompt
    assert "缺失不等于原文不存在" in prompt
    assert "命中不等于违规" in prompt
    assert "不能直接等同" in prompt
    assert (outline, brief) == original
