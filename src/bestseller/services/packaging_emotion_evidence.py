"""Bounded, source-text evidence for packaging situation/emotion signals.

This module does not judge reader emotion or override the deterministic appeal
gate.  It only validates two independent critic receipts when they point to
the same, literal situation evidence.  An absent or uncertain claim remains
the existing baseline rather than becoming a pass.
"""

from __future__ import annotations

# ruff: noqa: RUF001 — Chinese evidence prompts are intentional.
from collections.abc import Mapping, Sequence
import hashlib
from typing import Any
import unicodedata
from uuid import UUID

_CATEGORIES = ("两难", "切肤", "迫近", "骇异")
_CATEGORY_SET = frozenset(_CATEGORIES)
_SITUATIONS = {
    "两难": "choice_and_loss",
    "切肤": "relationship_stakes",
    "迫近": "active_threat",
    "骇异": "violated_expectation",
}
_ELEMENTS = {
    "两难": ("choice_quote", "personal_loss_quote"),
    "切肤": ("relationship_quote", "stakes_quote"),
    "迫近": ("threat_action_quote", "target_or_deadline_quote"),
    "骇异": ("abnormal_action_quote", "baseline_quote"),
}
_MAX_QUOTE_CHARS = 80
_BASELINE = 1.5


def render_emotion_evidence_messages(synopsis: str) -> tuple[str, str]:
    """Render a critic request without embedding a score or a pass instruction."""

    text = str(synopsis or "")
    system = (
        "你是简介情境证据检察员，不评榜单、不评文学质量、不打总分。"
        "只判断简介中是否存在明确可见的情境类别，并为每项摘录连续原文。"
        "不确定就不要声称存在。只输出JSON。"
    )
    user = (
        "情境类别仅限：两难、切肤、迫近、骇异。两难必须同时有明确选择与个人不可逆损失；"
        "切肤必须有具体关系者与其生死或得失；迫近必须有具体威胁行动与目标或期限；"
        "骇异必须有可见异常动作/变化和正常基准。静态刀具、普通职业介绍、泛泛危机、"
        "‘很危险/很悲伤’等情绪词堆砌都不算。\n"
        "分类冲突时按行为主体判定：同一处原文若既写保护关系者又写主角主动牺牲"
        "自己不可逆的财产、生计或生命，归两难，不重复归切肤；只有关系者自身的"
        "生死得失成为焦点且未写主角主动牺牲时，归切肤。明确做出牺牲也是选择，"
        "不要求原文出现‘二选一’字样。\n"
        "请检查文本中所有彼此不重叠的明确情境，不只挑最显著的一项；没有充分证据的类别不要凑数。\n"
        "两难字段为 choice_quote、personal_loss_quote；切肤字段为 relationship_quote、"
        "stakes_quote；迫近字段为 threat_action_quote、target_or_deadline_quote；"
        "骇异字段为 abnormal_action_quote、baseline_quote。每个字段都必须是"
        "evidence_quote 内的连续子串，不能跨句拼接。命令或条件不能推断成主角主动承担"
        "个人不可逆损失，不得在 reason 中补写原文没有给出的所有权或后果。\n"
        "每项必须给 category、situation、evidence_quote、elements、reason。"
        "situation 必须按类别填写：两难=choice_and_loss，切肤=relationship_stakes，"
        "迫近=active_threat，骇异=violated_expectation。请根据可见行为、后果和基准判断，"
        "不要用关键词堆砌代替情境证据。"
        "不要把同一段原文同时用于不同类别；不认识的类别不要输出。\n"
        f"简介：\n{text}\n"
        '输出：{"claims":[{"category":"两难|切肤|迫近|骇异",'
        '"situation":"choice_and_loss|relationship_stakes|active_threat|violated_expectation",'
        '"elements":{"choice_quote":"...","personal_loss_quote":"..."},'
        '"evidence_quote":"连续原文","reason":"基于原文的简短说明"}]}'
    )
    return system, user


def _uuid_text(value: object) -> str:
    try:
        parsed = UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return ""
    return str(parsed) if parsed.int else ""


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _claim_shape(raw: object) -> tuple[str, str, str, str, dict[str, str]]:
    if not isinstance(raw, Mapping):
        return "", "", "", "", {}
    raw_elements = raw.get("elements")
    elements = (
        {
            str(key): str(value or "").strip()
            for key, value in raw_elements.items()
            if str(value or "").strip()
        }
        if isinstance(raw_elements, Mapping)
        else {}
    )
    return (
        str(raw.get("category") or "").strip(),
        str(raw.get("situation") or "").strip(),
        str(raw.get("evidence_quote") or "").strip(),
        str(raw.get("reason") or "").strip(),
        elements,
    )


def _quote_span(quote: str, synopsis: str) -> tuple[int, int] | None:
    if not quote or len(quote) > _MAX_QUOTE_CHARS:
        return None
    start = synopsis.find(quote)
    return None if start < 0 else (start, start + len(quote))


def _score(categories: Sequence[str], spans: Sequence[tuple[int, int]]) -> tuple[float, bool]:
    front = any(end <= 30 for _start, end in spans)
    value = _BASELINE + min(len(set(categories)), 4) * 0.85 + (0.6 if front else 0)
    return min(5.0, value), front


def validate_emotion_evidence(raw: Mapping[str, Any] | None, synopsis: str) -> dict[str, Any]:
    """Validate one critic response; invalid claims never contribute to score."""

    text = str(synopsis or "")
    payload = raw if isinstance(raw, Mapping) else {}
    expected_hash = _text_hash(text)
    if "candidate_hash" in payload and str(payload.get("candidate_hash") or "") != expected_hash:
        return _empty_report("candidate_hash_mismatch")
    raw_claims = payload.get("claims")
    if not isinstance(raw_claims, Sequence) or isinstance(raw_claims, (str, bytes)):
        return _empty_report("claims_missing")

    accepted: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = []
    seen_quotes: set[str] = set()
    seen_categories: set[str] = set()
    spans: list[tuple[int, int]] = []
    for item in raw_claims:
        category, situation, quote, reason, elements = _claim_shape(item)
        failure = ""
        if category not in _CATEGORY_SET:
            failure = "unknown_category"
        elif situation != _SITUATIONS[category]:
            failure = "invalid_situation_for_category"
        elif set(elements) != set(_ELEMENTS[category]) or any(
            value not in quote for value in elements.values()
        ):
            failure = "invalid_category_elements"
        elif not quote or not reason:
            failure = "missing_claim_field"
        elif quote in seen_quotes or category in seen_categories:
            failure = "duplicate_category_or_quote"
        else:
            span = _quote_span(quote, text)
            if span is None:
                failure = "quote_not_continuous_or_too_long"
            elif any(_overlap(span, accepted_span) for accepted_span in spans):
                failure = "overlapping_quote_across_categories"
        if failure:
            rejected.append(
                {
                    "category": category,
                    "situation": situation,
                    "evidence_quote": quote,
                    "elements": elements,
                    "reason": failure,
                }
            )
            continue
        assert category and quote
        span = _quote_span(quote, text)
        assert span is not None
        seen_quotes.add(quote)
        seen_categories.add(category)
        spans.append(span)
        accepted.append(
            {
                "category": category,
                "situation": situation,
                "evidence_quote": quote,
                "elements": elements,
                "reason": reason,
            }
        )

    score, front = _score([item["category"] for item in accepted], spans)
    return {
        "valid": bool(accepted),
        "categories": [item["category"] for item in accepted],
        "claims": accepted,
        "rejected": rejected,
        "score": score,
        "front_loaded": front,
        "schema_version": "packaging-emotion-evidence.v1",
    }


def _empty_report(reason: str) -> dict[str, Any]:
    return {
        "valid": False,
        "categories": [],
        "claims": [],
        "rejected": [{"reason": reason}],
        "score": _BASELINE,
        "front_loaded": False,
        "schema_version": "packaging-emotion-evidence.v1",
    }


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return max(a[0], b[0]) < min(a[1], b[1])


def _sufficient_quote_overlap(
    left_quote: str,
    left_span: tuple[int, int],
    right_quote: str,
    right_span: tuple[int, int],
    synopsis: str,
) -> bool:
    start = max(left_span[0], right_span[0])
    end = min(left_span[1], right_span[1])
    if end <= start:
        return False
    overlap_text = synopsis[start:end]
    meaningful = sum(not unicodedata.category(char).startswith("P") for char in overlap_text)
    return meaningful >= 4 and meaningful >= min(len(left_quote), len(right_quote)) / 2


def corroborate_emotion_evidence(
    reviews: Sequence[Mapping[str, Any]] | None,
    synopsis: str,
    receipt_ids: Sequence[object] | None,
) -> dict[str, Any]:
    """Adopt only claims independently repeated on overlapping literal quotes."""

    rows = list(reviews or ())
    text = str(synopsis or "")
    candidate_hash = _text_hash(text)
    receipts = [_uuid_text(value) for value in (receipt_ids or ())]
    if len(rows) != 2 or len(receipts) != 2 or not all(receipts) or len(set(receipts)) != 2:
        return _empty_report("two_distinct_receipts_required")
    review_receipts = [_uuid_text(row.get("llm_run_id")) for row in rows]
    if (
        review_receipts != receipts
        or len(set(review_receipts)) != 2
        or any(str(row.get("candidate_hash") or "") != candidate_hash for row in rows)
    ):
        return _empty_report("review_receipts_do_not_match")

    normalized = [validate_emotion_evidence(row, text) for row in rows]
    if not all(row["valid"] for row in normalized):
        return _empty_report("one_review_has_no_valid_claim")
    corroborated_by_category: dict[str, tuple[dict[str, str], tuple[int, int]]] = {}
    for left in normalized[0]["claims"]:
        left_span = _quote_span(left["evidence_quote"], text)
        if left_span is None:
            continue
        for right in normalized[1]["claims"]:
            if left["category"] != right["category"]:
                continue
            right_span = _quote_span(right["evidence_quote"], text)
            if right_span is None or not _sufficient_quote_overlap(
                left["evidence_quote"],
                left_span,
                right["evidence_quote"],
                right_span,
                text,
            ):
                continue
            selected_claim = left
            quote = left["evidence_quote"]
            if len(right["evidence_quote"]) > len(quote):
                selected_claim = right
                quote = right["evidence_quote"]
            claim = {
                "category": selected_claim["category"],
                "situation": selected_claim["situation"],
                "evidence_quote": quote,
                "elements": selected_claim["elements"],
                "reason": f"两份独立回执一致指向{left['category']}情境",
            }
            quote_span = _quote_span(quote, text)
            assert quote_span is not None
            current = corroborated_by_category.get(left["category"])
            if current is None or len(quote) > len(current[0]["evidence_quote"]):
                corroborated_by_category[left["category"]] = (claim, quote_span)
            break

    selected = list(corroborated_by_category.values())
    if any(
        _overlap(left_span, right_span)
        for index, (_left_claim, left_span) in enumerate(selected)
        for _right_claim, right_span in selected[index + 1 :]
    ):
        return _empty_report("corroborated_quotes_overlap_across_categories")
    if not selected:
        return _empty_report("no_overlapping_same_category_claim")
    corroborated = [claim for claim, _span in selected]
    spans = [span for _claim, span in selected]
    score, front = _score([item["category"] for item in corroborated], spans)
    return {
        "valid": True,
        "corroborated": True,
        "categories": [item["category"] for item in corroborated],
        "claims": corroborated,
        "rejected": [],
        "score": score,
        "front_loaded": front,
        "receipt_ids": receipts,
        "candidate_hash": candidate_hash,
        "schema_version": "packaging-emotion-evidence.v1",
    }


__all__ = [
    "corroborate_emotion_evidence",
    "render_emotion_evidence_messages",
    "validate_emotion_evidence",
]
