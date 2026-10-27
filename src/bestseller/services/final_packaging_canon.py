"""Prompt builders for final-package facts against the approved canonical body.

This module deliberately does not judge literary quality or user prohibitions.
It gives the packaging reviewer one narrow responsibility: determine whether a
core story assertion in the reader-facing title/blurb is supported by the
approved story body.  The canonical body is supplied by the caller and must not
include the pending synopsis under review.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import copy
import json
import re
from typing import Any


_CANONICAL_SCHEMA = (
    '{"passed":true,"claims":[{"candidate_quote":"简介中的原文",'
    '"canonical_quote":"canonical body 中的精确原文或空字符串",'
    '"canonical_quotes":["复合 claim 可使用的多段精确原文"],'
    '"claim_type":"core_case|character|relationship|goal|mechanism|other",'
    '"status":"supported|unsupported|contradicted|omitted",'
    '"scope":"title|synopsis","reason":"逐项依据"}],'
    '"hard_conflicts":[{"classification":"hard_conflict","scope":"synopsis",'
    '"fact":"unsupported 的核心断言","generated":"简介原文",'
    '"reason":"canonical body 没有支持该核心属性"}],'
    '"repair_directives":["只删除或改写 unsupported/contradicted 的核心断言"],'
    '"upstream_consistency_warnings":[]}'
)


def _base_system(*, arbitration: bool) -> str:
    role = "仲裁官" if arbitration else "事实核对审查员"
    return (
        f"你是最终包装 canonical-only {role}。你的唯一职责是核对读者可见的书名和简介，"
        "不能评估文笔、市场吸引力或用户禁项，也不能用旧 seed/prohibitions 逻辑替代事实核对。"
        "canonical body 是唯一故事事实依据；待审 synopsis 永远不是 canonical body。\n\n"
        "先把 canonical body 中可核验的核心事实抽取为四类：核心案件性质/受害主体、角色身份与关系、"
        "主角目标、核心机制。逐项检查 title 和 synopsis 的核心故事断言，并为每项返回 candidate_quote、"
        "canonical_quote、claim_type、status、scope 和 reason。status 只能是 supported、unsupported、"
        "contradicted、omitted：omitted 只表示简介合法省略，不是失败；非核心场景表达可以自由改写。"
        "泛化事件或题材氛围不能支撑一个新增的核心案件属性、角色身份、关系、目标或机制；"
        "‘没有矛盾’、‘没有明确禁止’、‘题材上可能发生’都不能作为 supported 的依据。"
        "必须保留原句的认识状态：疑问、猜测、条件假设不等于肯定结论。把已有线索指向的可能性"
        "写成悬念问句是合法压缩，不能说问号把猜测定性了；但问句中预设的新增案件性质、受害者身份"
        "等核心事实仍必须有依据。分别检查问句的预设事实与尚待解答的结论，不得混为一谈。"
        "引文必须是连续原文，不拼接多处、不使用省略号；一个复合 claim 可以用 canonical_quotes 返回多段"
        "彼此独立的连续原文，每段都必须逐字存在；标签和台词装饰无需逐条列为核心断言。"
        "unsupported 或 contradicted 的核心断言必须进入对应 title 或 synopsis scope 的 hard_conflicts，"
        "并提供简介原文与 canonical 原文（若不存在则为空）的精确引用和理由。"
        "合理省略不得升级为 hard_conflict。不要为修复而新增故事事实。只输出 JSON。\n\n"
        + ("这是对初审报告的独立复核；可以推翻其 passed 结论，但不能放行 unsupported 核心断言。\n\n"
           if arbitration else "")
        + f"返回 schema：{_CANONICAL_SCHEMA}"
    )


def _effect_feedback_block(selected_effects: Sequence[Any] | None) -> str:
    effects = [str(item).strip() for item in (selected_effects or ()) if str(item).strip()]
    if not effects:
        return ""
    return (
        "\n另设一个与事实门禁完全独立的质量反馈区，只反馈已选中的效果技能："
        f"{json.dumps(effects, ensure_ascii=False)}。对每个效果返回 effect_realization，"
        "字段为 passed、evidence_quote、expectation、misalignment、reaction、"
        "situation_change。evidence_quote 必须逐字来自最终书名或简介；效果 passed=false、"
        "证据不足或风格不佳不得改变事实 claims、hard_conflicts 或事实 passed。"
    )


def _quote_text(text: str) -> str:
    """Normalize transport/layout only, retaining every story word."""
    return "".join(text.replace('\\"', '"').translate(str.maketrans({
        "“": '"', "”": '"', "‘": "'", "’": "'",
    })).split())


def canonical_source_catalog(canonical_body: str) -> dict[str, str]:
    """Extract stable IDs for original string leaves in the canonical assets."""
    values: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            values.append(value)
        elif isinstance(value, Mapping):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    chunks = re.split(r"\n\n(?=【终稿资产：)", canonical_body)
    for chunk in chunks:
        raw = chunk.split("\n", 1)[1] if "\n" in chunk else chunk
        try:
            visit(json.loads(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            # Truncated asset JSON still yields safely decodable original
            # string values; these remain subject to exact body validation.
            for match in re.findall(r'"(?:\\.|[^"\\])*"', raw):
                try:
                    visit(json.loads(match))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
    body_layout = _quote_text(canonical_body)
    catalog: dict[str, str] = {}
    for value in values:
        encoded = json.dumps(value, ensure_ascii=False)[1:-1]
        if (_quote_text(value) not in body_layout and _quote_text(encoded) not in body_layout):
            continue
        if value not in catalog.values():
            catalog[f"source_{len(catalog) + 1}"] = value
    return catalog


def _user_prompt(
    *,
    canonical_body: str,
    title: str,
    synopsis: str,
    challenged_report: Mapping[str, Any] | None,
) -> str:
    report_block = ""
    if challenged_report is not None:
        report_block = (
            "\n\n初审报告（仅供逐项复核，不是事实来源）：\n"
            f"{json.dumps(dict(challenged_report), ensure_ascii=False)}"
        )
    return (
        "已批准 canonical body（不含待审 synopsis）：\n"
        f"{canonical_body}\n\n"
        f"最终书名：{title}\n"
        f"待审上架简介：{synopsis}"
        f"{report_block}"
    )


def build_canonical_review_messages(
    *,
    canonical_body: str,
    title: str,
    synopsis: str,
    challenged_report: Mapping[str, Any] | None = None,
    selected_effects: Sequence[Any] | None = None,
) -> tuple[str, str]:
    """Build the first-pass canonical fact review messages."""

    return (
        _base_system(arbitration=False) + _effect_feedback_block(selected_effects),
        _user_prompt(
            canonical_body=canonical_body,
            title=title,
            synopsis=synopsis,
            challenged_report=challenged_report,
        ),
    )


def build_canonical_arbitration_messages(
    *,
    canonical_body: str,
    title: str,
    synopsis: str,
    challenged_report: Mapping[str, Any] | None = None,
    selected_effects: Sequence[Any] | None = None,
) -> tuple[str, str]:
    """Build an independent arbitration prompt with the same fact authority."""

    return (
        _base_system(arbitration=True) + _effect_feedback_block(selected_effects),
        _user_prompt(
            canonical_body=canonical_body,
            title=title,
            synopsis=synopsis,
            challenged_report=challenged_report,
        ),
    )


def build_canonical_quote_recovery_messages(
    *,
    canonical_body: str,
    title: str,
    synopsis: str,
    claims: list[Mapping[str, Any]],
    invalid_claim_indexes: list[int],
) -> tuple[str, str]:
    """Build a bounded quote-only evidence correction request.

    This is deliberately a separate, smaller request than the fact judge.  It
    may repair transport/layout damage (or a model's ellipsis-joined quote),
    but it is never allowed to change a claim's meaning or verdict.
    """
    system = (
        "你是 canonical-only 引文校正器。你只能校正指定 claim 的 candidate_quote、canonical_quote "
        "或 canonical_quotes，使它们分别成为简介/书名与 canonical body 中已经存在的连续原文。"
        "不得改写、增删或重排 claim，不得修改 status、reason、claim_type、scope，也不得创造故事事实。"
        "损坏的 canonical_quote 可以根据同一 claim 的事实含义重新定位到 canonical body 中措辞不同但"
        "真实存在的连续片段；禁止改写的是来源原文和 claim 语义，不是强行保留错误引文。"
        "不得用省略号拼接远处文字，不得模糊匹配；单双引号和排版空白可以按原文连续片段恢复。"
        "只从用户提供的原文片段目录选择 canonical_source_ids，不得重新抄写或拼接来源文字。"
        "一个复合 claim 如果需要多处依据，返回多个 canonical_source_ids；代码会从目录映射为 canonical_quotes。"
        "每段必须独立连续，禁止省略号拼接。只输出 JSON：{\"corrections\":[{\"index\":0,"
        "\"candidate_quote\":\"...\",\"canonical_source_ids\":[\"source_1\"]}],"
        "\"unrecoverable\":[{\"index\":0,"
        "\"reason\":\"没有找到连续原文\"}]}。每个指定 index 必须出现在 corrections 或"
        "unrecoverable 中，不能返回空列表。"
    )
    selected = [
        {"index": index, "claim": claims[index]}
        for index in invalid_claim_indexes
        if 0 <= index < len(claims) and isinstance(claims[index], Mapping)
    ]
    catalog = canonical_source_catalog(canonical_body)
    user = (
        "canonical body（唯一事实来源）：\n"
        f"{canonical_body}\n\n书名：{title}\n简介：{synopsis}\n\n"
        "canonical 原文片段目录（只能选择这些 ID；不得自行抄写 canonical_quote）：\n"
        f"{json.dumps(catalog, ensure_ascii=False)}\n\n"
        "只校正以下无效 claim 的引文字段：\n"
        f"{json.dumps(selected, ensure_ascii=False)}"
    )
    return system, user


def normalize_canonical_report(
    payload: Mapping[str, Any], *, title: str, synopsis: str, canonical_body: str,
) -> dict[str, Any]:
    """Ground core assertions without applying user-seed omission heuristics."""
    report = dict(payload)
    claims = payload.get("claims")
    verified = []
    conflicts = []
    invalid_indexes: list[int] = []
    exact_quote_recoveries: list[int] = []
    normalized_claims = copy.deepcopy(claims) if isinstance(claims, list) else []
    quote_text = _quote_text

    for index, claim in enumerate(claims if isinstance(claims, list) else []):
        if not isinstance(claim, Mapping):
            invalid_indexes.append(index)
            continue
        status = claim.get("status")
        if status == "omitted" or claim.get("claim_type") == "other":
            continue
        scope = claim.get("scope")
        surface = title if scope == "title" else synopsis if scope == "synopsis" else ""
        quote = str(claim.get("candidate_quote") or "").strip()
        raw_sources = claim.get("canonical_quotes")
        has_source_list = isinstance(raw_sources, list)
        sources = [str(item).strip() for item in raw_sources] if has_source_list else []
        source = str(claim.get("canonical_quote") or "").strip()
        if has_source_list and not sources:
            # Absence of a source is the expected evidence shape for an
            # unsupported assertion. It must reach content repair, not quote
            # recovery. Supported claims still require a real source below.
            has_source_list = False
        candidate_is_in_surface = bool(quote) and quote_text(quote) in quote_text(surface)
        source_is_in_canonical = (
            all(bool(item) and quote_text(item) in quote_text(canonical_body) for item in sources)
            if has_source_list
            else bool(source) and quote_text(source) in quote_text(canonical_body)
        )
        # If the model selected a verbatim canonical sentence but returned a
        # broken source quote (often an ellipsis join), recover the source by
        # exact retrieval of that already-present sentence. This is evidence
        # recovery, not semantic matching or a status change.
        if (
            status == "supported"
            and candidate_is_in_surface
            and not has_source_list
            and quote_text(quote) in quote_text(canonical_body)
            and not source_is_in_canonical
        ):
            source = quote
            normalized_claims[index]["canonical_quote"] = quote
            exact_quote_recoveries.append(index)
            source_is_in_canonical = True
        if not candidate_is_in_surface or ((source or has_source_list) and not source_is_in_canonical):
            invalid_indexes.append(index)
            continue
        if status not in {"supported", "unsupported", "contradicted"}:
            invalid_indexes.append(index)
            continue
        if status == "supported" and not source and not has_source_list:
            invalid_indexes.append(index)
            continue
        verified.append(dict(claim))
        if status in {"unsupported", "contradicted"} and claim.get("claim_type") in {
            "core_case", "character", "relationship", "goal", "mechanism",
        }:
            conflicts.append({
                "classification": "hard_conflict", "scope": scope,
                "source": "canonical_claim_grounding", "fact": "；".join(sources) if has_source_list else source,
                "generated": quote, "reason": str(claim.get("reason") or status),
                "claim_status": status,
            })
    report.update(
        # Preserve original claim positions and fields. A correction pass is
        # allowed to touch only quote fields at the reported indexes.
        claims=normalized_claims,
        hard_conflicts=conflicts,
        invalid_claim_indexes=invalid_indexes,
        exact_quote_recoveries=exact_quote_recoveries,
        dropped_unverified=len(invalid_indexes),
        passed=bool(verified) and not invalid_indexes and not conflicts,
    )
    if not verified or invalid_indexes:
        report["evaluator_error"] = "canonical_claim_evidence_incomplete"
    elif report.get("evaluator_error") == "canonical_claim_evidence_incomplete":
        report.pop("evaluator_error")
    report["repair_directives"] = [
        f"只修正包装原文「{item['generated']}」：{item['reason']}，不得改动故事事实基线。"
        for item in conflicts
    ]
    return report
