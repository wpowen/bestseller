"""Plan-guided, source-bound evidence; plans never substitute for observed prose."""

import json
import re

from .ability_methods import ability_terms
from .core_ability import core_source, verify_core_use
from .promise_io import precise_ask
from .storage import save

# Only local, explicit operations with a result in the same narrative sentence.
# A plan chooses search terms, never supplies an action, an outcome or causality.
_OPERATION = r"借用?|运用|动用|使出|施展|发动|催动|激活"
_OUTCOME = r"撞开|推开|撬开|击退|挡住|救出|斩断|切断|辨出|识破|看清|看见|探出|找出|发现|夺回|挣脱|冲出|清晰|分明"
_NEGATIVE = r"没有|并未|不曾|未曾|尚未|如果|假如|想起|打算|准备|失败|失效|无效|未能|没能|改用|转而|另用|尝试|猜测|回忆|当年|曾经|谈起|提及|提到|故事|传说|据说|设想"
_PLAN_FIELDS = ("number", "core_required", "core_application", "core_development")


def find_core_evidence(text, bible, plan=None):
    plan = plan if isinstance(plan, dict) else {}
    result = dict(
        status="unknown",
        source=core_source(bible),
        authority="agent_editorial_not_human",
        evidence_method="plan_guided_literal",
        validation_version=3,
        plan={k: plan[k] for k in _PLAN_FIELDS if k in plan},
    )
    application = plan.get("core_application")
    if not isinstance(application, str) or not application.strip():
        return dict(result, reason="missing_saved_application")
    terms = sorted((t for t in ability_terms(bible) if t in application), key=lambda t: (-len(t), t))
    for match in re.finditer(r"[^。！？!?\n]+[。！？!?]?", text):
        quote = match.group().strip()
        if len(quote) < 8 or re.search(_NEGATIVE, quote) or re.search(r'[“”「」『』"？?]', quote):
            continue
        for term in terms:
            # Same-sentence explicit use + concrete result, or the named power as
            # grammatical cause. Mere nearby action/outcome mentions do not count.
            use = re.search(r"(?:" + _OPERATION + r")[^，,；;。]{0,12}" + re.escape(term), quote)
            bound = re.search(re.escape(term) + r"[^，,；;。]{0,16}(?:使|令|让|从而|迫使)", quote)
            outcome = re.search(_OUTCOME, re.split(r"[，,；;]", quote[use.end() :], maxsplit=1)[0]) if use else None
            caused_result = bound and re.search(_OUTCOME, quote[bound.end() :])
            if not caused_result and not outcome:
                continue
            if not use and not bound:
                continue
            return dict(
                result,
                status="verified",
                mechanism=term,
                action_quote=quote,
                result_quote=quote,
                evidence_status="Evidence",
                causal_basis="same_sentence_explicit_operation_and_result",
            )
    return dict(result, reason="no_literal_plan_action_result")


def read_core_use(raw, text, bible, plan=None):
    found = find_core_evidence(text, bible, plan)
    if found["status"] == "verified":
        return dict(found, raw=raw if isinstance(raw, dict) else {})
    saved = verify_core_use(raw, text, bible)
    if saved["status"] != "unknown":
        return dict(saved, plan=found["plan"], evidence_method="saved_model_quotes")
    raw = raw if isinstance(raw, dict) else {}
    quotes = [raw.get(k) for k in ("action_quote", "result_quote")]
    if not raw or not any(isinstance(q, str) and q.strip() and q not in ("Unknown", "unknown") for q in quotes):
        reason = "empty_response"
    elif any(isinstance(q, str) and q.strip() and q not in text for q in quotes):
        reason = "quote_mismatch"
    else:
        reason = "unproven_action_or_causality"
    return dict(saved, plan=found["plan"], unknown_reason=reason, plan_search=found)


def observe_core_use(raw, text, bible, plan, client, prefix, ask):
    result = read_core_use(raw, text, bible, plan)
    if not core_source(bible) or not any(k in plan for k in ("core_required", "core_application")):
        return result
    if result["status"] == "unknown" and core_source(bible):
        response = precise_ask(
            ask,
            client,
            prefix + "-core-evidence-review",
            "只核对正文实际核心能力操作与决定结果，不修改正文。章纲仅用于查找；"
            "复制连续原文action_quote/result_quote，mechanism须圣经原词且出现在操作引文；"
            "结果需明确因果；否定、计划、失败、普通工具替代不能算成功。无证据填null。",
            [
                ("只读本章能力计划", json.dumps(result["plan"], ensure_ascii=False)),
                ("核心能力", json.dumps(core_source(bible), ensure_ascii=False)),
                ("完整正文", text),
            ],
            '{"decisive":null,"mechanism":null,"action_quote":null,"result_quote":null}',
            cap=1800,
        )
        result = dict(
            read_core_use(response, text, bible, plan),
            prior_extraction=raw,
            fallback_response=response,
            fallback_attempted=True,
        )
    save(client.out / "steps" / f"{prefix}-core-evidence.json", result)
    return result
