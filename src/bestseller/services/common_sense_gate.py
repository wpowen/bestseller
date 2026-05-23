"""Genre-aware common-sense consistency gate for generated prose.

The gate is intentionally narrow. It does not reject supernatural, xianxia,
fantasy, or horror premises for violating real-world physics. It only reports
local textual contradictions that should hold inside any genre unless the prose
itself supplies a visible cause, rule, or cost.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

Severity = Literal["low", "medium", "high"]


class CommonSenseFinding(BaseModel):
    code: str
    severity: Severity
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class CommonSenseGateReport(BaseModel):
    passed: bool
    genre_mode: str = "default"
    findings: list[CommonSenseFinding] = Field(default_factory=list)


def evaluate_common_sense_gate(
    text: str,
    *,
    genre: str | None = None,
    sub_genre: str | None = None,
    chapter_number: int | None = None,
) -> CommonSenseGateReport:
    content = text or ""
    findings: list[CommonSenseFinding] = []
    genre_mode = _genre_mode(genre, sub_genre)

    findings.extend(_find_unexplained_bleeding(content))
    findings.extend(_find_ambiguous_or_conflicting_countdown(content))
    findings.extend(_find_remaining_time_arithmetic(content))
    findings.extend(_find_coin_state_jump(content))
    findings.extend(_find_stitched_chapter_markers(content, chapter_number=chapter_number))
    findings.extend(_find_unintroduced_mentor_reference(content, chapter_number=chapter_number))
    findings.extend(_find_repeated_rescue_or_debt_beat(content, chapter_number=chapter_number))
    findings.extend(_find_early_chapter_character_crowding(content, chapter_number=chapter_number))
    findings.extend(_find_rule_term_onboarding_failure(content, chapter_number=chapter_number))

    blocking = [finding for finding in findings if finding.severity in {"high", "medium"}]
    return CommonSenseGateReport(
        passed=not blocking,
        genre_mode=genre_mode,
        findings=findings,
    )


def _genre_mode(genre: str | None, sub_genre: str | None) -> str:
    raw = f"{genre or ''} {sub_genre or ''}".lower()
    if any(token in raw for token in ("玄幻", "仙侠", "fantasy", "xianxia", "奇幻")):
        return "speculative"
    if any(token in raw for token in ("灵异", "horror", "supernatural", "惊悚")):
        return "supernatural"
    return "default"


def _find_unexplained_bleeding(text: str) -> list[CommonSenseFinding]:
    findings: list[CommonSenseFinding] = []
    for match in re.finditer(r"(鼻血|流血|渗血|出血|血滴|血顺着)", text):
        window = text[max(0, match.start() - 220) : match.end() + 90]
        if _has_bleeding_cause(window):
            continue
        findings.append(
            CommonSenseFinding(
                code="unexplained_body_state",
                severity="high",
                message="出现出血/鼻血，但附近没有伤口、撞击、术法反噬或代价来源。",
                evidence={"marker": match.group(1), "window": window.strip()},
            )
        )
    return findings


def _has_bleeding_cause(window: str) -> bool:
    return any(
        token in window
        for token in (
            "割",
            "划",
            "咬",
            "撞",
            "砸",
            "磕",
            "硌",
            "刺",
            "裂",
            "伤",
            "反噬",
            "代价",
            "符",
            "咒",
            "阴气",
            "灵压",
            "术法",
        )
    )


def _find_remaining_time_arithmetic(text: str) -> list[CommonSenseFinding]:
    checkpoints: list[tuple[int, str, int]] = []
    for match in re.finditer(
        r"(还剩|剩余|只剩下|只剩)\s*([零〇一二两三四五六七八九十百\d半个半\s]+)\s*(分钟|分|小时|个小时)",
        text,
    ):
        minutes = _parse_duration_minutes(match.group(2), match.group(3))
        if minutes is not None:
            checkpoints.append((match.start(), "remaining", minutes))
    for match in re.finditer(
        r"(过了|用了|耗了|骑了|骑|走了|跑了|车程|路上|赶路|赶了|对话)"
        r"[^零〇一二两三四五六七八九十百\d]{0,12}"
        r"([零〇一二两三四五六七八九十百\d半个半\s]+)\s*(分钟|分|小时|个小时)",
        text,
    ):
        minutes = _parse_duration_minutes(match.group(2), match.group(3))
        if minutes is not None:
            checkpoints.append((match.start(), "elapsed", minutes))
    checkpoints.sort(key=lambda item: item[0])

    findings: list[CommonSenseFinding] = []
    for index, (pos, kind, minutes) in enumerate(checkpoints):
        if kind != "remaining":
            continue
        elapsed = 0
        for _next_pos, next_kind, next_minutes in checkpoints[index + 1 :]:
            if next_kind == "elapsed":
                elapsed += next_minutes
                continue
            if next_kind != "remaining":
                continue
            expected_max = minutes - elapsed
            if elapsed > 0 and next_minutes > expected_max + 2:
                findings.append(
                    CommonSenseFinding(
                        code="remaining_time_arithmetic_conflict",
                        severity="high",
                        message="剩余时间与已消耗时间算不平。",
                        evidence={
                            "first_remaining_minutes": minutes,
                            "elapsed_minutes": elapsed,
                            "later_remaining_minutes": next_minutes,
                            "first_position": pos,
                            "later_position": _next_pos,
                        },
                    )
                )
            break
    return findings


def _find_ambiguous_or_conflicting_countdown(text: str) -> list[CommonSenseFinding]:
    findings: list[CommonSenseFinding] = []
    prior_small_remaining = bool(
        re.search(
            r"(十五分钟|十四分钟|两分钟|二分钟|还剩\s*[一二两三四五六七八九十\d]+\s*分钟)",
            text,
        )
    )
    for match in re.finditer(r"\b(\d{1,2}):(\d{2}):(\d{2})\b", text):
        window = text[max(0, match.start() - 32) : match.end() + 32]
        if "倒计时" not in window:
            continue
        hours = int(match.group(1))
        minutes = int(match.group(2))
        total_minutes = hours * 60 + minutes
        if prior_small_remaining and total_minutes > 120:
            findings.append(
                CommonSenseFinding(
                    code="countdown_scale_conflict",
                    severity="high",
                    message="正文先给出分钟级剩余时间，后续倒计时却显示小时级数值。",
                    evidence={"countdown": match.group(0), "window": window.strip()},
                )
            )
    return findings


def _parse_duration_minutes(raw_value: str, unit: str) -> int | None:
    value = raw_value.strip().replace(" ", "")
    if not value:
        return None
    if value in {"半", "半个"}:
        number = 0.5
    elif value.isdigit():
        number = int(value)
    else:
        parsed = _parse_chinese_number(value)
        if parsed is None:
            return None
        number = parsed
    multiplier = 60 if "小时" in unit else 1
    return int(number * multiplier)


def _parse_chinese_number(value: str) -> int | None:
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if value in digits:
        return digits[value]
    if "十" in value:
        left, _, right = value.partition("十")
        tens = digits.get(left, 1) if left else 1
        ones = digits.get(right, 0) if right else 0
        return tens * 10 + ones
    return None


def _find_coin_state_jump(text: str) -> list[CommonSenseFinding]:
    patterns = (
        r"(把|将|拿起)?(那枚)?康熙铜钱[^。！？\n]{0,18}(按|嵌|压|扣|贴)在[^。！？\n]{0,18}"
        r"[。！？\n][^。！？\n]{0,80}(从口袋里?|又从口袋里?|摸出|掏出)(那枚)?康熙铜钱",
        r"(把|将|拿起)?铜钱[^。！？\n]{0,18}(按|嵌|压|扣|贴)在[^。！？\n]{0,18}"
        r"[。！？\n][^。！？\n]{0,80}(从口袋里?|又从口袋里?|摸出|掏出)(那枚)?铜钱",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.S)
        if match:
            return [
                CommonSenseFinding(
                    code="object_state_jump",
                    severity="medium",
                    message="铜钱刚被放置/嵌入后，又在未取回的情况下从口袋摸出。",
                    evidence={"window": match.group(0).strip()},
                )
            ]
    return []


def _find_stitched_chapter_markers(
    text: str,
    *,
    chapter_number: int | None,
) -> list[CommonSenseFinding]:
    findings: list[CommonSenseFinding] = []
    if chapter_number is not None and chapter_number <= 3:
        abrupt_markers = (
            "外卖小哥被拖进墙里后",
            "当前存活",
            "已淘汰",
            "剩余时间",
        )
        found = [marker for marker in abrupt_markers if marker in text]
        if found:
            findings.append(
                CommonSenseFinding(
                    code="early_chapter_game_or_stitch_marker",
                    severity="medium",
                    message="前三章出现游戏化 UI 或未铺垫的拼接式事件标记。",
                    evidence={"markers": found},
                )
            )
    return findings


def _find_unintroduced_mentor_reference(
    text: str,
    *,
    chapter_number: int | None,
) -> list[CommonSenseFinding]:
    if chapter_number is None or chapter_number > 3:
        return []
    match = re.search(r"师父.{0,8}(教|说|留下|交代)", text)
    if not match:
        return []
    prior = text[: match.start()]
    if "师父" in prior:
        return []
    return [
        CommonSenseFinding(
            code="unintroduced_authority_reference",
            severity="medium",
            message="前三章突然使用未铺垫的师父/权威来源解释动作。",
            evidence={"window": text[max(0, match.start() - 40) : match.end() + 40].strip()},
        )
    ]


def _find_repeated_rescue_or_debt_beat(
    text: str,
    *,
    chapter_number: int | None,
) -> list[CommonSenseFinding]:
    if chapter_number is None or chapter_number > 5:
        return []
    rescue_hits = re.findall(r"陈默的身体[^。！？\n]{0,24}(弹|滚|摔|滑脱)", text)
    debt_push_hits = re.findall(r"(替.{0,8}押|账印|押上|替.{0,8}认)", text)
    if len(rescue_hits) >= 2 and len(debt_push_hits) >= 3:
        return [
            CommonSenseFinding(
                code="repeated_rescue_or_debt_beat",
                severity="medium",
                message="同一章内救人/押账节拍重复出现，疑似多版片段拼接。",
                evidence={
                    "rescue_hit_count": len(rescue_hits),
                    "debt_push_hit_count": len(debt_push_hits),
                },
            )
        ]
    return []


def _find_early_chapter_character_crowding(
    text: str,
    *,
    chapter_number: int | None,
) -> list[CommonSenseFinding]:
    if chapter_number is None or chapter_number > 3:
        return []
    role_markers = (
        "小雨",
        "陈默",
        "老道士",
        "老张",
        "眼镜男",
        "眼镜男生",
        "女白领",
        "老太太",
        "情侣",
        "外卖小哥",
    )
    present = [marker for marker in role_markers if marker in text]
    if len(present) < 5:
        return []
    onboarding_markers = ("走进", "进来", "出现", "介绍", "叫", "名叫", "是", "身份", "为什么")
    weakly_onboarded = []
    for marker in present:
        first = text.find(marker)
        window = text[max(0, first - 60) : first + len(marker) + 80]
        if not any(token in window for token in onboarding_markers):
            weakly_onboarded.append(marker)
    if len(weakly_onboarded) < 3:
        return []
    return [
        CommonSenseFinding(
            code="early_character_crowding",
            severity="medium",
            message="前三章一次性涌入过多人物，且多名人物缺少入场来源、身份或剧情功能说明。",
            evidence={
                "characters_or_roles": present,
                "weakly_onboarded": weakly_onboarded,
            },
        )
    ]


def _find_rule_term_onboarding_failure(
    text: str,
    *,
    chapter_number: int | None,
) -> list[CommonSenseFinding]:
    if chapter_number is None or chapter_number > 3:
        return []
    rule_terms = (
        "认账",
        "认葬",
        "入账",
        "否认者",
        "代认",
        "替认",
        "账主",
        "债主",
        "回执",
        "镜债",
        "血亲债",
    )
    present = [term for term in rule_terms if term in text]
    total_hits = sum(text.count(term) for term in rule_terms)
    if len(present) < 4 and total_hits < 10:
        return []
    weak_terms = []
    for term in present:
        first = text.find(term)
        window = text[max(0, first - 100) : first + len(term) + 140]
        if not _has_rule_onboarding(window):
            weak_terms.append(term)
    if len(weak_terms) < 2 and total_hits < 14 and len(present) < 7:
        return []
    return [
        CommonSenseFinding(
            code="rule_term_onboarding_failure",
            severity="medium",
            message="前三章规则术语密度过高，且缺少面向读者的定义、例子或可见代价台阶。",
            evidence={
                "rule_terms": present,
                "total_hits": total_hits,
                "weak_terms": weak_terms,
            },
        )
    ]


def _has_rule_onboarding(window: str) -> bool:
    return any(
        marker in window
        for marker in (
            "意思是",
            "也就是说",
            "规则",
            "如果",
            "就会",
            "不是",
            "而是",
            "代价",
            "先",
            "再",
            "所以",
            "例如",
            "比如",
            "因为",
        )
    )


__all__ = [
    "CommonSenseFinding",
    "CommonSenseGateReport",
    "evaluate_common_sense_gate",
]
