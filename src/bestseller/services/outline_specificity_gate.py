from __future__ import annotations

from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
import re
from typing import Any

from bestseller.domain.gate_verdict import GateFinding, GateVerdict
from bestseller.domain.workflow import ChapterOutlineInput
from bestseller.services.scene_plan_richness import (
    GENERIC_STATE_PATTERNS,
    GENERIC_STORY_PATTERNS,
)

PLACEHOLDER_BLACKLIST: tuple[str, ...] = (
    *GENERIC_STORY_PATTERNS,
    *GENERIC_STATE_PATTERNS,
    "写前补齐",
    "写前指定",
    "本章只推进",
    "阶段性兑现",
    "接住上一章具体尾钩",
    "落一个现实物证或状态变化",
    "让主角主动判断并付出代价",
    "完成一个阶段性兑现后再抛下一章钩子",
    "推动本章剧情发展",
)

_ENTITY_RE = re.compile(
    r"(?:[赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜谢邹喻柏水窦云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄和穆萧尹林钟徐邱高夏蔡田胡凌霍][一-龥]{1,3})"
    r"|(?:[0-9]{1,2}:[0-9]{2})"
    r"|(?:[0-9]{2,4}(?:号|栋|楼|层|室|门|房|点|时))"
    # Genre-neutral concrete entity: any quoted name/object (works for every genre,
    # replacing the old hardcoded 青囊 prop list 青囊/账印/回执/镜片/玉镯/账页/旧卷).
    r"|(?:[「『“”\"'《【][^」』“”\"'》】\n]{1,8}[」』“”\"'》】])"
    # Plus broadly-concrete physical nouns common across genres.
    r"|(?:尸体|钥匙|监控|门牌|证件|手机|钱包|照片|信封|纸条|血迹|伤口|令牌|印记|契约|凭证|遗物)"
)


def evaluate_outline_specificity(
    chapter_outline: ChapterOutlineInput | Mapping[str, Any],
    *,
    prev_outline: ChapterOutlineInput | Mapping[str, Any] | None = None,
) -> GateVerdict:
    chapter_no = _int_field(chapter_outline, "chapter_number", "chapter_no") or 0
    fields = _outline_fields(chapter_outline)
    findings: list[GateFinding] = []

    for key, value in fields.items():
        hits = _placeholder_hits(value)
        if hits:
            hit_text = ", ".join(sorted(set(hits)))
            findings.append(
                GateFinding(
                    code="OUTLINE_PLACEHOLDER",
                    severity="critical",
                    message=(
                        f"chapter {chapter_no or '?'} {key} contains "
                        f"placeholder wording: {hit_text}"
                    ),
                    path=f"chapter:{chapter_no}:{key}" if chapter_no else key,
                    repair_action=(
                        "rewrite the outline field with concrete people, "
                        "evidence, place, time, and payoff"
                    ),
                )
            )

    combined_text = "\n".join(_stringify(value) for value in fields.values())
    if combined_text.strip() and not _has_named_entity(combined_text):
        findings.append(
                GateFinding(
                    code="OUTLINE_LACKS_NAMED_ENTITY",
                    severity="critical",
                    message=(
                        f"chapter {chapter_no or '?'} outline lacks a named "
                        "person, object, place, or time anchor"
                    ),
                    path=f"chapter:{chapter_no}" if chapter_no else "",
                    repair_action=(
                        "add at least one concrete named entity such as a "
                        "character, object, location, room number, or clock time"
                    ),
                )
            )

    duplicate_ratio = 0.0
    if prev_outline is not None:
        duplicate_ratio = _duplicate_ratio(
            _field(chapter_outline, "scene_beats", "scenes"),
            _field(prev_outline, "scene_beats", "scenes"),
        )
        if duplicate_ratio >= 0.8:
            findings.append(
                GateFinding(
                    code="OUTLINE_BEATS_DUPLICATE_PREV",
                    severity="high",
                    message=(
                        f"chapter {chapter_no or '?'} scene beats duplicate "
                        f"previous chapter at {duplicate_ratio:.0%}"
                    ),
                    path=f"chapter:{chapter_no}:scene_beats" if chapter_no else "scene_beats",
                    repair_action=(
                        "replace repeated beat templates with this chapter's "
                        "unique action, evidence, and handoff"
                    ),
                )
            )

    total_chars = len(combined_text)
    placeholder_hits = sum(len(_placeholder_hits(value)) for value in fields.values())
    coverage = 1.0 if not findings else max(0.0, 1.0 - min(1.0, len(findings) / 3))
    verdict = (
        "blocked"
        if any(f.severity == "critical" for f in findings)
        else ("warn_only" if findings else "pass")
    )
    return GateVerdict(
        gate_name="outline_specificity_gate",
        verdict=verdict,
        coverage=coverage,
        findings=tuple(findings),
        metrics={
            "chapter_no": chapter_no,
            "placeholder_hits": placeholder_hits,
            "total_chars": total_chars,
            "duplicate_ratio_prev": duplicate_ratio,
            "specificity_score": _specificity_score(total_chars, placeholder_hits, findings),
        },
    )


def _outline_fields(outline: ChapterOutlineInput | Mapping[str, Any]) -> dict[str, Any]:
    contract = _field(outline, "causal_contract")
    if not isinstance(contract, Mapping):
        contract = {}
    fields: dict[str, Any] = {
        "chapter_objective": _field(outline, "chapter_objective", "chapter_goal", "goal"),
        "scene_beats": _field(outline, "scene_beats", "scenes"),
        "required_evidence": _field(outline, "required_evidence"),
        "required_payoff": _field(outline, "required_payoff", "hook_description"),
    }
    for key in ("chapter_objective", "scene_beats", "required_evidence", "required_payoff"):
        if not fields.get(key):
            fields[key] = contract.get(key)
    return fields


def _field(outline: ChapterOutlineInput | Mapping[str, Any], *names: str) -> object:
    for name in names:
        if isinstance(outline, Mapping):
            if name in outline:
                return outline.get(name)
        else:
            value = getattr(outline, name, None)
            if value is not None:
                return value
    return None


def _int_field(outline: ChapterOutlineInput | Mapping[str, Any], *names: str) -> int | None:
    value = _field(outline, *names)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _placeholder_hits(value: object) -> tuple[str, ...]:
    text = _stringify(value)
    if not text.strip():
        return ()
    return tuple(pattern for pattern in PLACEHOLDER_BLACKLIST if pattern and pattern in text)


def _has_named_entity(text: str) -> bool:
    return bool(_ENTITY_RE.search(text))


def _duplicate_ratio(current: object, previous: object) -> float:
    current_text = _normalize_for_similarity(current)
    previous_text = _normalize_for_similarity(previous)
    if not current_text or not previous_text:
        return 0.0
    return SequenceMatcher(None, current_text, previous_text).ratio()


def _normalize_for_similarity(value: object) -> str:
    return re.sub(r"\s+", "", _stringify(value).lower())


def _stringify(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return "\n".join(f"{key}: {_stringify(item)}" for key, item in value.items())
    if isinstance(value, Sequence):
        return "\n".join(_stringify(item) for item in value)
    return str(value)


def _specificity_score(
    total_chars: int,
    placeholder_hits: int,
    findings: Sequence[GateFinding],
) -> float:
    if total_chars <= 0:
        return 0.0
    penalty = placeholder_hits * 0.25 + len(findings) * 0.2
    return max(0.0, min(1.0, 1.0 - penalty))
