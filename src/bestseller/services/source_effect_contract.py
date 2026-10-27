"""Source-bound primary ability direction contract.

This contract deliberately checks one explicit causal direction and one literal
source quote.  It does not claim to prove an operation chain, character
identity, or complete story fidelity; those remain responsibilities of the
other review gates.
"""

# The current extraction prompt is frozen against the definition-first v2 real probe.
# Its Chinese punctuation is part of the provider evidence contract.
# ruff: noqa: RUF001

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import re
from typing import Any
from uuid import UUID, uuid4

_CAUSAL_MODES = frozenset(
    {
        "causes_target_action",
        "perceives_information",
        "changes_target_state",
        "communicates_information",
        "other",
        "unknown",
    }
)
_PRIMARY_MODES = _CAUSAL_MODES - {"other", "unknown"}
_ORIGINS = frozenset({"source", "candidate"})
_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _uuid_text(value: object) -> str:
    try:
        parsed = UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        return ""
    normalized = str(parsed)
    return normalized if parsed.int and _UUID_PATTERN.fullmatch(normalized) else ""


def _valid_identity(contract: Mapping[str, Any], expected_origin: str) -> bool:
    return (
        contract.get("origin") == expected_origin
        and bool(_uuid_text(contract.get("contract_id")))
        and bool(_uuid_text(contract.get("llm_run_id")))
        and bool(_HASH_PATTERN.fullmatch(str(contract.get("text_hash") or "")))
    )


_DEFINITION_EVIDENCE_RULES = (
    '\n能力证据选择规则：\n先找明确说明核心能力如何起作用的定义或实际使用句，优先选同时说明作用'
    '目标和直接结果的连续原文。\n区分能力的直接作用与使用后的调查发现：通过能力获得线索、作出判'
    '断或看到后果，不自动等于能力本身是感知信息。\n不能用普通调查、观察、判断句替代文中已经明确'
    '写出的能力定义；不得因某段剧情写得更长就选它。\n若同一核心能力在不同字段有互不相容的明确作'
    '用定义，返回 applicability=unknown、causal_mode=unkn'
    'own 并说明冲突；不要只挑一处正确定义掩盖其他定义。\n没有特殊机制的普通现实叙事仍返回n'
    'ot_applicable；明确存在特殊机制但直接作用无法判断则unknown。\nappl'
    'icability字段严格区分：能力定义互相矛盾时必须unknown，不能标not_app'
    'licable；只有普通现实叙事完全没有特殊机制才not_applicable。\n当app'
    'licability为unknown或not_applicable时，mechanism_'
    'quote必须是空字符串，causal_mode必须unknown，理由写入applica'
    'bility_reason。\n'
)


def render_source_effect_extraction_prompt(*, text: str, origin: str) -> tuple[str, str]:
    """Return the definition-first extraction prompt validated by real controls."""

    return (
        "你是小说设定事实抽取员。只读取给定文本，不改写故事。只输出JSON。",
        f"""从下面文本找出明确写出的主角核心能力，摘录说明能力实际作用的连续原文。
只抽核心能力如何起作用，不把施法代价、背景设定或普通剧情行动另算能力。
能力在一次剧情中被使用，仍然是能力；不能因使用次数少而判not_applicable。
输出：{{"applicability":"applicable|not_applicable|unknown","applicability_reason":"理由","mechanism_quote":"一处完整连续原文","causal_mode":"枚举"}}
causal_mode只选实际作用方向：
- causes_target_action：使目标执行或完成一个动作；
- perceives_information：主角看到、听到或感知信息；
- changes_target_state：直接改变目标的状态或物质；
- communicates_information：向目标传递信息；
- other：明确能力但不属于以上方向；
- unknown：无法判断。
同一能力在多个字段重复描述只取一处最清楚的原文，不拼接不同字段。
只返回核心能力最主要的一个作用方向和一处原文，不枚举重复描述或普通剧情行动。
这只是主能力方向检查，不承担内部多步骤顺序或完整保真审查。
只有完全没有明确能力/特殊机制的普通现实叙事才not_applicable，并说明理由。
不要添加文本没写的能力，不评价小说质量。
{_DEFINITION_EVIDENCE_RULES}输入来源：{origin}
原文：
{text}""",
    )


def normalize_source_effect_contract(
    payload: Mapping[str, Any] | None,
    *,
    source_text: str,
    origin: str,
    llm_run_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Normalize one primary direction result, failing closed on evidence."""

    text = str(source_text or "")
    raw = payload if isinstance(payload, Mapping) else {}
    applicability = str(raw.get("applicability") or "unknown").strip().lower()
    if applicability not in {"applicable", "not_applicable", "unknown"}:
        applicability = "unknown"
    reason = str(raw.get("applicability_reason") or "").strip()
    quote = str(raw.get("mechanism_quote") or "").strip()
    mode = str(raw.get("causal_mode") or "unknown").strip().lower()
    if mode not in _CAUSAL_MODES:
        mode = "unknown"
    operations_declared = bool(raw.get("operations"))
    receipt = _uuid_text(llm_run_id)
    quote_valid = bool(quote) and quote in text
    evidence_complete = bool(
        applicability == "applicable" and mode in _PRIMARY_MODES and quote_valid
    )
    not_applicable = (
        applicability == "not_applicable"
        and bool(reason)
        and mode == "unknown"
        and not quote
        and not operations_declared
    )
    known = bool(receipt) and (evidence_complete or not_applicable)
    operation = {
        "mechanism_quote": quote if quote_valid else "",
        "causal_mode": mode,
        "evidence_complete": evidence_complete,
    }
    return {
        "contract_id": str(uuid4()),
        "llm_run_id": receipt,
        "origin": origin if origin in _ORIGINS else "unknown",
        "text_hash": _hash_text(text),
        "applicability": applicability,
        "applicability_reason": reason,
        "mechanism_quote": operation["mechanism_quote"],
        "causal_mode": mode,
        "known": known,
        # Kept as a one-element compatibility view for existing callers.
        "operations": [operation] if applicability == "applicable" else [],
    }


def compare_source_effect_contracts(
    source: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare only the primary causal direction and its literal evidence."""

    source_receipt = _uuid_text(source.get("llm_run_id"))
    candidate_receipt = _uuid_text(candidate.get("llm_run_id"))
    if not source_receipt or not candidate_receipt or source_receipt == candidate_receipt:
        return {
            "status": "unknown",
            "hard_conflicts": [],
            "reason": "source and candidate require distinct non-nil extraction receipts",
        }
    if (
        not _valid_identity(source, "source")
        or not _valid_identity(candidate, "candidate")
        or source.get("contract_id") == candidate.get("contract_id")
    ):
        return {
            "status": "unknown",
            "hard_conflicts": [],
            "reason": "source/candidate origin, hash, or contract identity is invalid",
        }
    if source.get("applicability") == "not_applicable" and source.get("known") is True:
        return {
            "status": "not_applicable",
            "hard_conflicts": [],
            "reason": "source does not state a repeatable core ability or mechanism",
        }
    if source.get("known") is not True or candidate.get("known") is not True:
        return {
            "status": "unknown",
            "hard_conflicts": [],
            "reason": "source or candidate primary direction evidence is incomplete",
        }
    source_mode = source.get("causal_mode")
    candidate_mode = candidate.get("causal_mode")
    if source_mode == candidate_mode:
        return {
            "status": "pass",
            "hard_conflicts": [],
            "source_contract_id": source.get("contract_id"),
            "candidate_contract_id": candidate.get("contract_id"),
        }
    return {
        "status": "conflict",
        "hard_conflicts": [
            {
                "source": "source_effect_contract",
                "reason": "核心能力的主要作用方向未保留",
                "classification": "causal_mode_mismatch",
                "fact": {
                    "causal_mode": source_mode,
                    "mechanism_quote": source.get("mechanism_quote", ""),
                },
                "generated": {
                    "causal_mode": candidate_mode,
                    "mechanism_quote": candidate.get("mechanism_quote", ""),
                },
            }
        ],
        "source_contract_id": source.get("contract_id"),
        "candidate_contract_id": candidate.get("contract_id"),
    }


def source_effect_contract_matches_source(
    contract: Mapping[str, Any] | None, source_text: str
) -> bool:
    """Validate a local extraction receipt against its exact source before reuse."""
    if (
        not source_text
        or not isinstance(contract, Mapping)
        or contract.get("known") is not True
        or not _valid_identity(contract, "source")
        or contract.get("text_hash") != _hash_text(source_text)
    ):
        return False
    checked = normalize_source_effect_contract(
        contract,
        source_text=source_text,
        origin="source",
        llm_run_id=contract.get("llm_run_id"),
    )
    return bool(
        checked["known"]
        and checked["operations"] == contract.get("operations")
        and checked["causal_mode"] == contract.get("causal_mode")
        and checked["mechanism_quote"] == contract.get("mechanism_quote")
    )


def render_source_effect_generation_block(
    contract: Mapping[str, Any] | None, *, source_text: str
) -> str:
    """Render only source evidence, never a candidate or a critic's interpretation."""
    if not source_effect_contract_matches_source(contract, source_text):
        return ""
    assert isinstance(contract, Mapping)
    if contract.get("applicability") != "applicable":
        return ""
    definitions = {
        "causes_target_action": "目标本身执行动作并产生实际结果",
        "perceives_information": "主角看到、听到或感知信息",
        "changes_target_state": "直接改变目标的状态或物质",
        "communicates_information": "向目标传递信息",
    }
    mode = str(contract["causal_mode"])
    return (
        "\n\n【SOURCE_ONLY_PRIMARY_CONTRACT】\n"
        f"source_quote=“{contract['mechanism_quote']}”\n"
        f"causal_mode={mode}\n"
        f"中文定义：{definitions[mode]}\n"
        "这是对上位创意核心动作方向的事实约束，只能保持该方向并补足目标、阻力、选择后果与可变形循环。\n"
    )
