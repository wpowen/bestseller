"""Future prompt authority, with the existing wire and numeric gates intact."""

# ruff: noqa: RUF001 — Chinese protocol text is intentional.
from copy import deepcopy
import hashlib
import json

import pytest

from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services import commercial_source_evidence as source
from bestseller.services.commercial_review_tool import DIMENSIONS, review_submission_tools
from bestseller.services.commercial_review_wire import WIRE_VERSION
from bestseller.services.llm import LLMCompletionResult
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
def test_future_protocol_distinguishes_creation_from_authority_and_historical_contradiction(
    language,
):
    prompt = judge._render_chapter_judge_system_prompt(
        rubric=None,
        reference_block="UNSELECTED_REFERENCE",
        checklist_block="UNSELECTED_CHECKLIST",
        calibration_block="UNSELECTED_CALIBRATION",
        genre_context=judge._neutral_genre_context(),
        language=language,
    )
    for phrase in (
        "允许在已成立的故事边界内创作新的当下或未来行动、人物、物件",
        "不要求在前章已出现或预先逐项写入批准清单",
        "Prior silence and open inventory observations are not closed sets or prohibitions",
        "Missing approval fields do not forbid new causal fictional development",
        "同一历史事件或同一时点、同一对象状态出现互斥陈述",
        "违反有明确来源的身份/世界不变量",
        "unexplained_transition 应指出前后状态之间缺少交代的必要因果环节",
        "不得把过渡缺环写成永久能力禁令",
        "可指出需要补足的功能性环节",
        "不要替作者编造具体的新人物、道具、能力、期限或模板剧情作为唯一解",
        "最终执行动作由服务端 action policy 决定",
        "不得把前文缺失、作者是否授权、能力是否允许或库存是否排他的事实/权威断言改标为narrative_quality",
        "不能代替结构化source_id",
    ):
        assert phrase in prompt
    for obsolete in (
        "沿用已授权人物、物件、规则和事件",
        "不得用新期限、新能力、物件异象",
        "不增添新人物/道具/能力/期限",
        "服务端按severity路由，critical/high阻断",
        "UNSELECTED_REFERENCE",
        "UNSELECTED_CHECKLIST",
        "UNSELECTED_CALIBRATION",
    ):
        assert obsolete not in prompt
    assert "same_event_or_state或unexplained_transition" in prompt
    assert "当前明确批准的硬条款优先" in prompt


def test_prompt_version_changes_source_protocol_identity_without_changing_sources_or_tool_schema(
    monkeypatch,
):
    kwargs = {
        "chapter_number": 2,
        "content_md": "她取出新做的木片。",
        "source": {},
        "previous_chapters": [{"chapter_number": 1, "body": "她昨日把匣子放在窗边。"}],
    }
    future = source.assemble_commercial_sources(**kwargs)
    assert source.PROMPT_VERSION == "v12-source-authority"
    assert future.receipt["prompt_version"] == source.PROMPT_VERSION
    with monkeypatch.context() as historical:
        historical.setattr(source, "PROMPT_VERSION", "v11-structured-review-tool")
        previous = source.assemble_commercial_sources(**kwargs)
    assert future.text == previous.text and future.catalog == previous.catalog
    assert future.receipt["source_protocol_sha256"] != previous.receipt["source_protocol_sha256"]
    assert review_submission_tools(future) == review_submission_tools(previous)
    assert WIRE_VERSION == "commercial-review-wire.v2"


@pytest.mark.asyncio
async def test_actual_judge_request_uses_v12_full_sources_and_preserves_negative_physical_result(
    monkeypatch,
):
    body = "她把新做的木片放进匣中。\n\n她听见响声，却没有看清声音从何处传来。"
    prior = "她昨日把匣子放在窗边。"
    requests = []
    raw_returns = []

    async def local_completion(session, settings, request):
        requests.append(request)
        payload = json.loads(
            request.user_prompt.split(
                "完整来源包（JSON 数据，含本章全文、已提供前章和类型化引用目录）：\n", 1
            )[1]
        )
        current = next(v for v in payload["citation_catalog"] if v["kind"] == "current")
        raw = json.dumps(
            {
                "wire_version": WIRE_VERSION,
                "pass": False,
                "overall_score": 0.61,
                "dimension_scores": dict.fromkeys(DIMENSIONS, 0.61),
                "issues": [
                    {
                        "code": "UNRESOLVED_READER_CAUSAL_LINK",
                        "severity": "high",
                        "assessment_kind": "narrative_quality",
                        "body_id": current["id"],
                        "reason": "读者不能理解行为与响声之间的联系。",
                        "required_fix": "交代行为与响声的联系，不指定具体剧情实现。",
                    }
                ],
                "rewrite_plan": {
                    "scope": "chapter",
                    "preserve": ["已写出的动作"],
                    "change": ["交代功能性因果环节"],
                    "instructions": "说明因果联系。",
                },
            },
            ensure_ascii=False,
        )
        calls = [
            {
                "id": "local-physical-submit",
                "type": "function",
                "function": {
                    "name": "submit_chapter_review",
                    "arguments": raw,
                },
            }
        ]
        raw_returns.append(deepcopy(calls))
        return LLMCompletionResult(
            content="",
            tool_calls=calls,
            raw_message={"role": "assistant", "content": "", "tool_calls": deepcopy(calls)},
            finish_reason="tool_calls",
            provider="mock",
            model_name="mock",
        )

    monkeypatch.setattr(judge, "complete_text", local_completion)
    result = await judge.judge_chapter_commercial_quality(
        object(),
        load_settings(env={}),
        chapter_number=2,
        content_md=body,
        previous_chapters=[{"chapter_number": 1, "content_md": prior}],
        sample_slot="slot-0",
    )
    assert len(requests) == 1
    request = requests[0]
    assert request.prompt_version == source.PROMPT_VERSION == "v12-source-authority"
    assert request.tool_choice == "required" and request.sample_slot == "slot-0"
    assert "最终执行动作由服务端 action policy 决定" in request.system_prompt
    assert "沿用已授权人物、物件、规则和事件" not in request.system_prompt
    payload = json.loads(
        request.user_prompt.split(
            "完整来源包（JSON 数据，含本章全文、已提供前章和类型化引用目录）：\n", 1
        )[1]
    )
    assert (
        payload["current_body"] == body and payload["previous_chapters"][0]["content_md"] == prior
    )
    receipt = request.metadata["commercial_source_input_receipt"]
    assert receipt["body_sha256"] == hashlib.sha256(body.encode()).hexdigest()
    assert receipt["budget_passed"]
    assert result.quality_rejected and not result.infrastructure_failure
    assert result.overall_score == 0.61 and result.dimension_scores == dict.fromkeys(
        DIMENSIONS, 0.61
    )
    assert [issue.code for issue in result.blocking_issues] == ["UNRESOLVED_READER_CAUSAL_LINK"]
    assert result.blocking_issues[0].severity == "high"
    # Structural/request proof only: the fixture is not an actual semantic evaluation.
    retained = json.loads(result.raw_excerpt)
    assert retained["raw_report"] == raw_returns[0][0]["function"]["arguments"]
